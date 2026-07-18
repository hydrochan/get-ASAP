"""비논문 Notion 페이지 정리 스크립트 (일회성 운영 스크립트, backfill_rsc.py/
relocate_june_backfill.py와 같은 성격).

배경:
  backfill_rsc.py(Crossref 백필)와 기존 메일 파이프라인 모두 "Outstanding
  Reviewers for {journal} in 2025"(RSC), "{journal} themed collection on
  ..."(RSC), 'Comment on "..."'(Chem Sci/Langmuir), 'Retraction notice to
  "..."'(Int J Hydrogen Energy) 같은 비논문 페이지를 이미 Notion에 저장한
  상태로 남겼다. parsers/filters.py의 SKIP_PREFIXES/SKIP_SUBSTRINGS를
  보강한 뒤(이 저장소의 filters.py 패치), 그 규칙에 걸리는 기존 페이지를
  캐시 CSV(cache/papers_YYYY-MM.csv)에서 찾아 Notion에서 아카이브한다.

사용법:
  python purge_non_articles.py             # 드라이런(기본): 콘솔 리포트만 출력
  python purge_non_articles.py --execute   # 실제 Notion 아카이브 실행
  python purge_non_articles.py --verbose   # DEBUG 레벨 로그 활성화

주의:
  state.json, Gmail 라벨은 이 스크립트가 절대 건드리지 않는다. 캐시(CSV)
  재생성도 이 스크립트가 하지 않는다 - 2시간 주기 refresh_csv cron이
  아카이브 이후 자연스럽게 다시 채운다.
"""
import argparse
import csv
import logging
import os
import sys
import time

import httpx

import config
from notion_auth import get_notion_client
from notion_client_mod import _call_with_retry, _find_monthly_db
from parsers.filters import (
    SKIP_PREFIXES,
    SKIP_SUBSTRINGS,
    SKIP_TITLES,
    is_valid_paper_title,
)

logger = logging.getLogger(__name__)


# ---------- 상수 ----------

# 이번 정리 대상 월(하드코딩) - relocate_june_backfill.py의
# SOURCE_MONTH/TARGET_MONTH와 같은 관례로, 일회성 운영 범위를 명시한다.
MONTHS = ["2026-06", "2026-07"]

# 서버 기준 상대경로 (backfill_rsc.py의 _DEFAULT_CACHE_DIR과 동일 위치)
_DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")

NOTION_VERSION = "2022-06-28"
RATE_LIMIT_SLEEP = 0.35
PROGRESS_INTERVAL = 100


# ---------- 캐시 CSV -> 비논문 후보 로드 ----------

def _load_candidate_rows(cache_dir: str) -> list[dict]:
    """MONTHS에 정의된 월별 캐시 CSV를 읽어 비논문(is_valid_paper_title=False) 행만 추출.

    각 행에 "_month"를 태그해 이후 월별 Notion DB 조회에 쓴다. 캐시 파일이
    없는 월은 경고만 남기고 건너뛴다(크래시하지 않음).
    """
    candidates: list[dict] = []

    for month in MONTHS:
        path = os.path.join(cache_dir, f"papers_{month}.csv")
        if not os.path.exists(path):
            logger.warning("캐시 파일 없음, 해당 월 스킵: %s", path)
            continue

        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = (row.get("title") or "").strip()
                if not title or is_valid_paper_title(title):
                    continue
                tagged = dict(row)
                tagged["_month"] = month
                candidates.append(tagged)
        logger.info("캐시 로드: %s", path)

    return candidates


def _classify_title(title: str) -> str:
    """드라이런 리포트의 "유형별 집계"용 분류 - 필터링 로직 자체는 아니다
    (실제 필터링은 항상 is_valid_paper_title 단일 판정을 따른다).

    어느 규칙이 걸렀는지 알 수 없는 경우(저널명 매칭/너무 짧음/단어 수 부족)는
    잔여 버킷으로 묶는다.
    """
    low = title.strip().lower()
    if low in SKIP_TITLES:
        return "SKIP_TITLES(정확 매칭)"
    for prefix in SKIP_PREFIXES:
        if low.startswith(prefix):
            return f"SKIP_PREFIXES: {prefix!r}"
    for substr in SKIP_SUBSTRINGS:
        if substr in low:
            return f"SKIP_SUBSTRINGS: {substr!r}"
    return "기타(저널명/짧음)"


# ---------- 리포트 출력 ----------

def _print_dry_run_report(candidates: list[dict]) -> None:
    print("\n=== 비논문 Notion 페이지 정리 리포트 (드라이런) ===")
    print(f"대상 월: {', '.join(MONTHS)}")

    print()
    col1, col2, col3 = 10, 30, 70
    header = f"{'월':<{col1}} {'저널':<{col2}} {'제목':<{col3}}"
    print(header)
    print("-" * len(header))
    for row in candidates:
        month = row.get("_month", "")
        journal = (row.get("journal") or "")[:col2]
        title = row.get("title") or ""
        print(f"{month:<{col1}} {journal:<{col2}} {title}")
    print("-" * len(header))

    print("\n=== 유형별 집계 ===")
    type_counts: dict[str, int] = {}
    for row in candidates:
        label = _classify_title(row.get("title") or "")
        type_counts[label] = type_counts.get(label, 0) + 1
    for label in sorted(type_counts):
        print(f"  {label}: {type_counts[label]}건")

    print(f"\n총 대상: {len(candidates)}건")
    print("(--execute 없이 실행되면 아카이브되지 않습니다)")


# ---------- Notion 조회 (httpx 직접 호출) ----------

def _query_db(database_id: str, filter_payload: dict) -> list[dict]:
    """Notion REST API POST /databases/{id}/query 직접 호출 (페이지네이션).

    relocate_june_backfill.py의 _query_db와 동일한 패턴(SDK의 data_sources.query
    필터는 신뢰할 수 없다고 기록되어 있어 httpx 직접 호출을 쓴다).
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
        payload = {"page_size": 100, "filter": filter_payload}
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


def _query_title_exact(database_id: str, title: str) -> list[dict]:
    """제목이 정확히 일치하는 페이지를 조회. 같은 제목이 복수 존재할 수 있어 전부 반환한다."""
    filter_payload = {"property": "Title", "title": {"equals": title}}
    return _query_db(database_id, filter_payload)


# ---------- 아카이브 실행 ----------

def _execute_purge(candidates: list[dict]) -> dict:
    """--execute 경로: 월별 DB id를 캐싱해가며 제목 정확 매칭 조회 후 전부 아카이브.

    - 같은 제목이 복수 매칭되면 전부 아카이브한다(어차피 비논문이므로 안전).
    - 월 DB를 찾지 못하면(_find_monthly_db가 None) 그 월의 후보는 전부
      경고만 남기고 스킵한다(실행 전체를 중단하지 않음).
    - Notion에서 페이지를 찾지 못해도 경고만 남기고 계속 진행한다.
    """
    client = get_notion_client()
    db_id_by_month: dict[str, str | None] = {}
    archived = not_found = 0
    total = len(candidates)

    for i, row in enumerate(candidates):
        month = row["_month"]
        title = row.get("title") or ""

        if month not in db_id_by_month:
            db_id_by_month[month] = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, month)
            if db_id_by_month[month] is None:
                logger.warning("월 DB(get-ASAP %s)를 찾을 수 없어 해당 월 전체를 스킵합니다.", month)

        db_id = db_id_by_month[month]
        if db_id is None:
            continue

        pages = _query_title_exact(db_id, title)
        time.sleep(RATE_LIMIT_SLEEP)

        if not pages:
            logger.warning("Notion에서 페이지를 찾지 못해 스킵: [%s] %s", month, title[:80])
            not_found += 1
        else:
            for page in pages:
                _call_with_retry(client.pages.update, page_id=page["id"], archived=True)
                time.sleep(RATE_LIMIT_SLEEP)
                archived += 1

        if (i + 1) % PROGRESS_INTERVAL == 0:
            logger.info("진행: %d/%d", i + 1, total)

    return {"archived": archived, "not_found": not_found}


# ---------- 로깅/인자 ----------

def _setup_logging(verbose: bool) -> None:
    """backfill_rsc.py/relocate_june_backfill.py와 동일한 콘솔 로깅 스타일. 파일
    핸들러 없이 콘솔 출력만 구성한다."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stdout,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=f"비논문 Notion 페이지 정리 스크립트 (대상 월: {', '.join(MONTHS)})",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="실제 Notion 아카이브 실행 (기본값은 드라이런)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        default=False,
        help="DEBUG 레벨 로그 활성화",
    )
    parser.add_argument(
        "--cache-dir",
        default=_DEFAULT_CACHE_DIR,
        help="비논문 후보 판정에 쓸 캐시 CSV 디렉터리 (기본: 서버 기준 ./cache)",
    )
    return parser.parse_args(argv)


# ---------- 진입점 ----------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    logger.info(
        "비논문 페이지 정리 시작 (대상 월: %s, execute=%s)",
        ", ".join(MONTHS), args.execute,
    )

    candidates = _load_candidate_rows(args.cache_dir)
    logger.info("비논문 후보 %d건 발견", len(candidates))

    _print_dry_run_report(candidates)

    if not args.execute:
        logger.info("드라이런 완료. 실제 아카이브는 --execute 플래그로 실행하세요.")
        return 0

    if not candidates:
        logger.info("아카이브할 후보가 없어 종료합니다.")
        return 0

    if not config.NOTION_PARENT_PAGE_ID:
        logger.error("NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. .env 파일에 설정하세요.")
        return 1

    result = _execute_purge(candidates)
    logger.info(
        "아카이브 완료: %d건 아카이브, %d건 미발견",
        result["archived"], result["not_found"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
