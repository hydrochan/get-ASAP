"""Gmail ASAP 파이프라인 오케스트레이터 (per D-01, D-02, D-03)

전체 실행 흐름:
  Gmail 메일 수신 -> 출판사별 파싱 -> Notion 저장 -> 메일 라벨 마킹

사용법:
  python main.py                  # 전체 파이프라인 실행
  python main.py --dry-run        # Notion 저장/라벨 없이 파싱 결과만 출력
  python main.py --verbose        # DEBUG 레벨 로그 활성화
"""
import argparse
import json
import logging
import logging.handlers
import os
import sys

from auth import get_gmail_service
from gmail_client import (
    build_query,
    extract_body,
    get_new_messages,
    get_or_create_label,
    infer_journal,
    load_state,
    mark_processed,
    save_state,
)
from notion_client_mod import get_or_create_db, save_papers
from parser_registry import load_parsers

# 모듈 전용 로거 (setup_logging 이후 사용)
logger = logging.getLogger(__name__)

# publishers.json 기본 경로 (이 파일 기준)
_PUBLISHERS_PATH = os.path.join(os.path.dirname(__file__), "publishers.json")


# ---------- 내부 헬퍼 ----------

def _load_publishers(path: str = None) -> dict:
    """publishers.json을 로드하여 딕셔너리 반환.

    Args:
        path: publishers.json 경로. None이면 모듈 디렉토리의 publishers.json 사용.

    Returns:
        출판사 설정 딕셔너리
    """
    if path is None:
        path = _PUBLISHERS_PATH
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_publisher_key(sender: str, publishers: dict) -> str | None:
    """발신자 이메일로 publishers dict에서 publisher key 반환.

    Args:
        sender: 발신자 이메일 주소
        publishers: 출판사 설정 딕셔너리

    Returns:
        매칭된 publisher key (예: "acs"), 없으면 None
    """
    # sender는 "Display Name <email>" 형식일 수 있으므로 이메일 부분만 추출하여 비교
    sender_lower = sender.lower()
    for key, pub_data in publishers.items():
        pub_sender = pub_data.get("sender", "").lower()
        if pub_sender in sender_lower:
            return key
    return None


def _extract_header(headers: list[dict], name: str) -> str:
    """Gmail 메시지 headers 목록에서 특정 헤더 값을 추출한다.

    Args:
        headers: Gmail API 메시지의 headers 리스트 (각 요소: {"name": ..., "value": ...})
        name: 찾을 헤더 이름 (대소문자 무시)

    Returns:
        헤더 값 문자열. 없으면 빈 문자열.
    """
    name_lower = name.lower()
    for header in headers:
        if header.get("name", "").lower() == name_lower:
            return header.get("value", "")
    return ""


# ---------- 로깅 설정 ----------

def setup_logging(verbose: bool = False) -> None:
    """파일 + 콘솔 동시 출력 로깅 설정 (per D-10, D-11, D-13).

    - 파일 핸들러: logs/get-asap.log, RotatingFileHandler(5MB, backup 3개)
    - 콘솔 핸들러: stdout
    - 기본 레벨: INFO, --verbose 시 DEBUG

    Args:
        verbose: True면 DEBUG 레벨, False면 INFO 레벨
    """
    # logs/ 디렉토리 없으면 생성
    logs_dir = os.path.join(os.path.dirname(__file__), "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_level = logging.DEBUG if verbose else logging.INFO
    log_format = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
    formatter = logging.Formatter(log_format)

    # 루트 로거 설정
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 기존 핸들러 제거 (중복 방지)
    root_logger.handlers.clear()

    # 파일 핸들러: RotatingFileHandler (per D-10, Claude's Discretion)
    log_file = os.path.join(logs_dir, "get-asap.log")
    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,  # 5MB
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root_logger.addHandler(file_handler)

    # 콘솔 핸들러: stdout (per D-11)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)


# ---------- argparse ----------

def parse_args(argv: list[str] = None) -> argparse.Namespace:
    """CLI 인자 파싱 (per D-02).

    Args:
        argv: 파싱할 인자 목록. None이면 sys.argv[1:] 사용.

    Returns:
        파싱된 Namespace (dry_run, verbose 속성 포함)
    """
    parser = argparse.ArgumentParser(
        description="Gmail ASAP 논문 알림 -> Notion 자동화 파이프라인",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=False,
        help="Notion 저장 + 라벨 마킹 없이 파싱 결과만 콘솔에 출력 (per D-02)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="DEBUG 레벨 로그 활성화 (per D-02, D-13)",
    )
    return parser.parse_args(argv)


# ---------- 파이프라인 ----------

def run_pipeline(dry_run: bool = False) -> dict:
    """Gmail ASAP 메일 수집 -> 파싱 -> DOI 조회 -> Notion 저장 전체 파이프라인 실행 (per D-01).

    실행 순서:
    1. publishers.json 로드
    2. Gmail 인증
    3. 검색 쿼리 생성
    4. 상태 로드 (증분 동기화용 historyId)
    5. 새 메일 ID 목록 가져오기
    6. 메일이 없으면 조기 종료
    7. 라벨 준비 (dry_run 아닐 때만)
    8. 파서 로드
    9. 각 메일에서 논문 추출 (에러 시 해당 메일만 스킵)
    10. dry_run: 콘솔 출력만 / 아니면: Notion 저장 + 라벨 마킹
    11. 상태 저장 + 실행 요약 로그

    Args:
        dry_run: True면 Notion 저장과 라벨 마킹을 스킵하고 콘솔에만 출력

    Returns:
        실행 요약 딕셔너리 {"extracted": N, "saved": N, "skipped": N, "failed": N}
    """
    # 1. publishers.json 로드
    publishers = _load_publishers()

    # 2. Gmail 인증
    service = get_gmail_service()

    # 3. 검색 쿼리 생성
    query = build_query(publishers)

    # 4. 상태 로드 (증분 동기화용 historyId)
    state = load_state()

    # 5. 새 메일 ID 목록 가져오기
    msg_ids = get_new_messages(service, state, query)
    logger.info("새 메일 %d건 발견", len(msg_ids))

    # 6. 메일이 없으면 조기 종료
    if not msg_ids:
        logger.info("새 메일 없음 - 파이프라인 종료")
        save_state(state)
        return {"extracted": 0, "saved": 0, "skipped": 0, "failed": 0}

    # 7. 라벨 준비 (dry_run 아닐 때만 API 호출 - 불필요한 API 요청 방지)
    label_id = None
    if not dry_run:
        label_id = get_or_create_label(service)

    # 8. 파서 로드 및 publisher key로 매핑
    parsers = load_parsers()
    parser_map = {p.publisher_name: p for p in parsers}

    # 9. 각 메일에서 논문 추출
    all_papers = []
    processed_msg_ids = []  # 성공적으로 처리된 메일 ID (라벨 마킹 대상)

    for msg_id in msg_ids:
        try:
            # 메일 전체 내용 가져오기 (full 포맷: headers + payload)
            msg = service.users().messages().get(
                userId="me", id=msg_id, format="full"
            ).execute()

            headers = msg["payload"].get("headers", [])
            sender = _extract_header(headers, "From")
            subject = _extract_header(headers, "Subject")

            # 발신자로 출판사 key 결정
            pub_key = _find_publisher_key(sender, publishers)
            if pub_key is None:
                logger.warning("알 수 없는 발신자, 스킵: %s", sender)
                continue

            # 해당 출판사 파서 찾기 (publishers.json의 name 필드로 매칭)
            pub_name = publishers[pub_key].get("name", "")
            parser = parser_map.get(pub_name)
            if parser is None:
                logger.warning("파서 없음 (publisher=%s), 스킵: %s", pub_name, msg_id)
                continue

            # 메일 본문 추출 및 파싱
            body = extract_body(msg["payload"])
            papers = parser.parse(body)
            logger.debug("메일 %s: %d건 추출", msg_id, len(papers))

            # 각 paper의 누락 필드 보완 (저널명만 — DOI는 ASAP 특성상 미등록이 많아 조회하지 않음)
            for paper in papers:
                if not paper.journal:
                    paper.journal = infer_journal(sender, subject, publishers)

            all_papers.extend(papers)
            processed_msg_ids.append(msg_id)

        except Exception as e:
            # 개별 메일 처리 실패 시 스킵하고 계속 진행 (per must_haves)
            logger.warning("메일 %s 처리 실패, 스킵: %s", msg_id, e)
            continue

    logger.info("총 %d건 논문 추출", len(all_papers))

    # 10. dry_run 분기
    if dry_run:
        # dry_run: Notion 저장 없이 콘솔 출력만 (per D-02)
        print(f"\n[DRY RUN] 추출된 논문 {len(all_papers)}건:")
        for paper in all_papers:
            print(f"  - 제목: {paper.title}")
            print(f"    DOI:   {paper.doi or '(없음)'}")
            print(f"    저널:  {paper.journal or '(없음)'}")
            print()

        save_state(state)
        result = {"extracted": len(all_papers), "saved": 0, "skipped": 0, "failed": 0}
    else:
        # 실제 실행: Notion 저장 + 라벨 마킹
        db_id = get_or_create_db()
        save_result = save_papers(all_papers, db_id)

        # 처리 완료된 메일에 라벨 마킹
        for msg_id in processed_msg_ids:
            try:
                mark_processed(service, msg_id, label_id)
            except Exception as e:
                logger.warning("라벨 마킹 실패 (msg_id=%s): %s", msg_id, e)

        save_state(state)
        result = {"extracted": len(all_papers), **save_result}

    # 11. 실행 요약 로그 (per D-12)
    logger.info(
        "완료: %d건 추출, %d건 저장, %d건 중복 스킵, %d건 실패",
        result["extracted"],
        result.get("saved", 0),
        result.get("skipped", 0),
        result.get("failed", 0),
    )

    return result


# ---------- 진입점 ----------

if __name__ == "__main__":
    args = parse_args()
    setup_logging(args.verbose)

    try:
        run_pipeline(dry_run=args.dry_run)
        sys.exit(0)
    except Exception as e:
        logger.exception("파이프라인 실행 중 치명적 오류 발생: %s", e)
        sys.exit(1)
