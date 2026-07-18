"""RSC 백필 오배치 정정 스크립트 (일회성 운영 스크립트, backfill_rsc.py와 같은 성격).

배경:
  backfill_rsc.py가 RSC 저널 공백기 논문을 Notion에 백필했는데, 저장에 쓴
  get_or_create_db()는 "실행 시점의 현재 월" DB만 반환하는 구조라, 6월 날짜
  논문까지 전부 "get-ASAP 2026-07" DB에 들어갔다. 하류 분류기(paper_autodown)는
  월별 DB를 "DB 제목의 월 = Date 윈도우"로 읽으므로, 7월 DB에 있는 6월 날짜
  페이지는 어떤 쿼리에도 걸리지 않는다. 이 스크립트는 그 페이지들을
  "get-ASAP 2026-06" DB로 옮긴다.

  Notion API에는 페이지를 다른 데이터베이스로 옮기는 기능이 없으므로,
  "6월 DB에 동일 속성으로 재생성 + 원본(7월 DB) 아카이브" 방식으로 구현한다.

사용법:
  python relocate_june_backfill.py             # 드라이런(기본): 콘솔 리포트만 출력
  python relocate_june_backfill.py --execute   # 실제 이사 실행
  python relocate_june_backfill.py --verbose   # DEBUG 레벨 로그 활성화

주의:
  state.json, Gmail 라벨, 캐시(CSV) 재생성은 이 스크립트가 건드리지 않는다.
  캐시는 2시간 주기 refresh_csv cron이 알아서 다시 채운다.
"""
import argparse
import logging
import sys
import time

import httpx

import config
from notion_auth import get_notion_client
from notion_client_mod import _call_with_retry, _find_monthly_db

logger = logging.getLogger(__name__)


# ---------- 상수 ----------

SOURCE_MONTH = "2026-07"  # 오배치가 발생한 DB (get-ASAP 2026-07)
TARGET_MONTH = "2026-06"  # 옮겨야 할 대상 DB (get-ASAP 2026-06)

# Date 프로퍼티가 이 값보다 이른(exclusive) 페이지만 대상 - 7월 DB에 잘못 들어간 6월 논문
DATE_CUTOFF = "2026-07-01"

# backfill_rsc.py --execute 실행 시작 시각은 2026-07-18T15:17:50Z (운영 로그 확인).
# 이보다 이전에 저장된 페이지는 이번 백필과 무관한 정상 데이터이므로 절대 건드리면
# 안 된다. 17분의 여유를 두고 15:00:00.000Z로 가드를 잡는다.
CREATED_TIME_GUARD = "2026-07-18T15:00:00.000Z"

NOTION_VERSION = "2022-06-28"
RATE_LIMIT_SLEEP = 0.35
PROGRESS_INTERVAL = 100


# ---------- Notion 조회 (httpx 직접 호출) ----------

def _query_db(database_id: str, filter_payload: dict | None = None) -> list[dict]:
    """Notion REST API POST /databases/{id}/query 직접 호출 (페이지네이션).

    notion_client_mod.py/analytics/notion_fetcher.py와 동일하게 SDK의
    data_sources.query 대신 httpx 직접 호출을 쓴다(설치된 notion-client
    3.0.0에는 databases.query 메서드 자체가 없고, data_sources.query의
    필터는 신뢰할 수 없다고 기록되어 있다).
    """
    headers = {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }
    url = f"https://api.notion.com/v1/databases/{database_id}/query"

    all_pages: list[dict] = []
    cursor = None
    while True:
        payload = {"page_size": 100}
        if filter_payload:
            payload["filter"] = filter_payload
        if cursor:
            payload["start_cursor"] = cursor
        resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        resp.raise_for_status()
        result = resp.json()
        all_pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return all_pages


def _relocation_candidates_filter() -> dict:
    """이사 대상 서버사이드 필터.

    세 조건 모두 만족: Date < DATE_CUTOFF, created_time >= CREATED_TIME_GUARD,
    GPT Reason 비어 있음(분류 전 원본만 - 추가 안전망). 서버사이드에서 최대한
    걸러내되, 필터 신뢰도가 낮다는 기존 기록(notion_client_mod.py 주석)을
    감안해 클라이언트에서 _passes_client_validation으로 재검증한다.
    """
    return {
        "and": [
            {"property": "Date", "date": {"before": DATE_CUTOFF}},
            {"timestamp": "created_time", "created_time": {"on_or_after": CREATED_TIME_GUARD}},
            {"property": "GPT Reason", "rich_text": {"is_empty": True}},
        ]
    }


# ---------- 페이지 <-> 필드 변환 ----------

def _extract_fields(page: dict) -> dict:
    """Notion page -> 이사에 필요한 필드 dict(title/journal/date/url/status/gpt_reason).

    analytics/notion_fetcher.py의 _parse_pages와 같은 속성 구조를 읽지만,
    DataFrame이 아니라 재생성에 그대로 쓸 원본 값을 보존한다.
    """
    props = page.get("properties", {})

    title_arr = props.get("Title", {}).get("title", []) or []
    title = "".join(seg.get("plain_text", "") for seg in title_arr)

    journal_sel = props.get("Journal", {}).get("select")
    journal = journal_sel.get("name", "") if journal_sel else ""

    date_obj = props.get("Date", {}).get("date")
    date_str = date_obj.get("start", "") if date_obj else ""

    url = props.get("URL", {}).get("url", "") or ""

    status_sel = props.get("Status", {}).get("select")
    status = status_sel.get("name", "") if status_sel else ""

    reason_arr = props.get("GPT Reason", {}).get("rich_text", []) or []
    gpt_reason = "".join(seg.get("plain_text", "") for seg in reason_arr)

    return {
        "title": title,
        "journal": journal,
        "date": date_str,
        "url": url,
        "status": status,
        "gpt_reason": gpt_reason,
    }


def _passes_client_validation(page: dict, fields: dict) -> bool:
    """서버 필터를 통과한 페이지를 클라이언트에서 재검증(신뢰도 낮은 필터 대비 안전망)."""
    date_str = fields["date"]
    if not date_str or date_str >= DATE_CUTOFF:
        return False

    created_time = page.get("created_time") or ""
    if created_time < CREATED_TIME_GUARD:
        return False

    if fields["gpt_reason"].strip():
        return False

    return True


def find_relocation_candidates(source_db_id: str) -> list[dict]:
    """소스(7월) DB에서 이사 대상 페이지 조회: 서버사이드 필터 + 클라이언트 재검증.

    반환: [{"page": <원본 Notion page 객체>, "fields": <추출된 필드 dict>}, ...]
    """
    pages = _query_db(source_db_id, _relocation_candidates_filter())
    candidates = []
    for page in pages:
        fields = _extract_fields(page)
        if not _passes_client_validation(page, fields):
            logger.warning(
                "서버 필터는 통과했지만 클라이언트 재검증 탈락, 스킵: page_id=%s title=%s",
                page.get("id"), fields["title"][:60],
            )
            continue
        candidates.append({"page": page, "fields": fields})
    return candidates


def _build_relocated_properties(fields: dict) -> dict:
    """소스 페이지에서 읽은 값을 그대로 6월 DB 스키마에 맞춰 복사.

    notion_client_mod._build_properties()는 Status를 항상 '대기중'으로
    고정하지만, 여기서는 원본 값을 그대로 옮기는 것이 목적이므로 소스 Status를
    보존한다(값이 비어 있으면 스키마 기본값인 '대기중'으로 폴백).
    """
    props: dict = {
        "Title": {"title": [{"type": "text", "text": {"content": fields["title"]}}]},
        "Status": {"select": {"name": fields["status"] or "대기중"}},
    }

    if fields["journal"]:
        props["Journal"] = {"select": {"name": fields["journal"]}}

    if fields["date"]:
        props["Date"] = {"date": {"start": fields["date"]}}

    if fields["url"]:
        props["URL"] = {"url": fields["url"]}

    if fields["gpt_reason"]:
        props["GPT Reason"] = {
            "rich_text": [{"type": "text", "text": {"content": fields["gpt_reason"]}}]
        }

    return props


# ---------- 재실행 안전(크래시 복구) ----------

def _load_existing_relocated_titles(target_db_id: str) -> set[str]:
    """재실행 안전장치.

    생성 성공 후 아카이브 실패로 프로세스가 죽으면, 재실행 시 같은 페이지를
    6월 DB에 중복 생성할 위험이 있다. 실행 시작 시 6월 DB에서
    created_time >= CREATED_TIME_GUARD인(=이 스크립트가 만들었을 가능성이
    있는) 페이지의 제목을 미리 로드해, 이미 존재하면 생성을 건너뛰고
    원본 아카이브만 재시도한다.
    """
    filter_payload = {
        "timestamp": "created_time",
        "created_time": {"on_or_after": CREATED_TIME_GUARD},
    }
    pages = _query_db(target_db_id, filter_payload)
    titles: set[str] = set()
    for page in pages:
        fields = _extract_fields(page)
        if fields["title"]:
            titles.add(fields["title"])
    return titles


# ---------- 이사 실행 ----------

def relocate_pages(
    candidates: list[dict], target_db_id: str, existing_titles: set[str]
) -> dict:
    """이사 실행: 6월 DB에 페이지 생성(재실행 시 스킵 가능) -> 성공 후에만 원본 아카이브.

    생성 실패 시 원본을 아카이브하지 않는다(데이터 유실 방지 - 실패한 페이지는
    7월 DB에 그대로 남아 다음 재실행 때 다시 시도된다).
    """
    client = get_notion_client()
    moved = skipped_create = archived = archive_failed = create_failed = 0
    total = len(candidates)

    for i, item in enumerate(candidates):
        page = item["page"]
        fields = item["fields"]
        page_id = page["id"]

        if (i + 1) % PROGRESS_INTERVAL == 0:
            logger.info("이사 진행: %d/%d", i + 1, total)

        if fields["title"] in existing_titles:
            logger.info(
                "6월 DB에 이미 생성됨(재실행), 원본 아카이브만 수행: %s",
                fields["title"][:60],
            )
            skipped_create += 1
        else:
            result = _call_with_retry(
                client.pages.create,
                parent={"database_id": target_db_id},
                properties=_build_relocated_properties(fields),
            )
            if result is None:
                logger.warning(
                    "6월 DB 페이지 생성 실패, 원본 유지(아카이브 안 함): %s",
                    fields["title"][:60],
                )
                create_failed += 1
                time.sleep(RATE_LIMIT_SLEEP)
                continue
            moved += 1
            existing_titles.add(fields["title"])

        archive_result = _call_with_retry(
            client.pages.update, page_id=page_id, archived=True,
        )
        if archive_result is None:
            logger.warning(
                "원본 아카이브 실패(생성은 완료됨, 재실행 시 스킵+아카이브 재시도됨): page_id=%s",
                page_id,
            )
            archive_failed += 1
        else:
            archived += 1

        time.sleep(RATE_LIMIT_SLEEP)

    return {
        "moved": moved,
        "skipped_create": skipped_create,
        "archived": archived,
        "archive_failed": archive_failed,
        "create_failed": create_failed,
    }


# ---------- 리포트 출력 ----------

def _print_dry_run_report(candidates: list[dict]) -> None:
    print("\n=== 6월 백필 이사 리포트 (드라이런) ===")
    print(f"소스: get-ASAP {SOURCE_MONTH}  ->  타깃: get-ASAP {TARGET_MONTH}")
    print(f"조건: Date < {DATE_CUTOFF}, created_time >= {CREATED_TIME_GUARD}, GPT Reason 비어있음")

    month_counts: dict[str, int] = {}
    for item in candidates:
        date_str = item["fields"]["date"]
        month = date_str[:7] if date_str else "(날짜 없음)"
        month_counts[month] = month_counts.get(month, 0) + 1

    print("\n[Date 월별 분포]")
    if not month_counts:
        print("  (대상 없음)")
    for month in sorted(month_counts):
        print(f"  {month}: {month_counts[month]}건")

    print("\n[샘플 제목 (최대 5개)]")
    if not candidates:
        print("  (대상 없음)")
    for item in candidates[:5]:
        f = item["fields"]
        print(f"  - {f['date']}  {f['title']}")

    print(f"\n총 이사 대상: {len(candidates)}건")
    print("(--execute 없이 실행되면 이사되지 않습니다)")


# ---------- 로깅/인자 ----------

def _setup_logging(verbose: bool) -> None:
    """backfill_rsc.py와 동일한 콘솔 로깅 스타일. 파일 핸들러 없이 콘솔 출력만 구성."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stdout,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RSC 백필 오배치 정정: get-ASAP 2026-07 DB의 6월 논문을 2026-06 DB로 이사",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="실제 이사 실행 (기본값은 드라이런)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="DEBUG 레벨 로그 활성화",
    )
    return parser.parse_args(argv)


# ---------- 진입점 ----------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    logger.info(
        "6월 백필 이사 시작 (get-ASAP %s -> get-ASAP %s, execute=%s)",
        SOURCE_MONTH, TARGET_MONTH, args.execute,
    )

    if not config.NOTION_PARENT_PAGE_ID:
        logger.error("NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. .env 파일에 설정하세요.")
        return 1

    source_db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, SOURCE_MONTH)
    if not source_db_id:
        logger.error("소스 DB(get-ASAP %s)를 찾을 수 없습니다.", SOURCE_MONTH)
        return 1

    target_db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, TARGET_MONTH)
    if not target_db_id:
        logger.error("타깃 DB(get-ASAP %s)를 찾을 수 없습니다.", TARGET_MONTH)
        return 1

    logger.info("소스 DB(get-ASAP %s)에서 이사 대상 조회 중...", SOURCE_MONTH)
    candidates = find_relocation_candidates(source_db_id)
    logger.info("이사 대상 %d건 확인", len(candidates))

    _print_dry_run_report(candidates)

    if not args.execute:
        logger.info("드라이런 완료. 실제 이사는 --execute 플래그로 실행하세요.")
        return 0

    if not candidates:
        logger.info("이사할 페이지가 없어 종료합니다.")
        return 0

    logger.info(
        "재실행 안전장치: 타깃 DB(get-ASAP %s)에서 기존 생성 페이지 제목 로드 중...",
        TARGET_MONTH,
    )
    existing_titles = _load_existing_relocated_titles(target_db_id)
    logger.info("기존 생성된 페이지 %d건 발견 (재실행 시 생성 스킵 대상)", len(existing_titles))

    result = relocate_pages(candidates, target_db_id, existing_titles)
    logger.info(
        "이사 완료: 신규 생성 %d건, 생성 스킵(재실행) %d건, 아카이브 %d건, "
        "아카이브 실패 %d건, 생성 실패 %d건",
        result["moved"], result["skipped_create"], result["archived"],
        result["archive_failed"], result["create_failed"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
