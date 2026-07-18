"""RSC 저널 공백기 백필 스크립트 (일회성 운영 스크립트, refresh_csv.py와 같은 성격).

배경:
  RSC가 2026-07에 알림 메일 플랫폼을 교체하면서 6월 중순~7월 14일 사이
  전 저널의 ToC 메일이 발송되지 않았다(예: EES는 6/9 Issue 11 이후 침묵,
  JMC-A는 Issue 37~40 건너뜀, Green Chem 24~26 등). 메일 자체가 없으므로
  Gmail 경로로는 복구 불가 -> Crossref works API로 해당 기간 논문을 직접
  수집해 Notion에 백필한다.

사용법:
  python backfill_rsc.py                 # 드라이런(기본): 콘솔 리포트만 출력
  python backfill_rsc.py --execute       # 실제 Notion 저장 + 캐시 재생성
  python backfill_rsc.py --verbose       # DEBUG 레벨 로그 활성화

주의:
  state.json, Gmail 라벨은 이 스크립트가 절대 건드리지 않는다(Gmail 경로와
  무관하게 Crossref로만 수집하므로 증분 동기화 상태와 엮일 이유가 없다).
"""
import argparse
import csv
import html
import logging
import os
import re
import sys
import time

import httpx
from bs4 import BeautifulSoup

from main import _is_excluded_journal, _refresh_cache_from_notion
from models import PaperMetadata
from notion_client_mod import _normalize_title_for_duplicate, get_or_create_db, save_papers
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


# ---------- 상수 ----------

# publishers.json의 rsc.journals(11종) 정식 명칭 -> Crossref ISSN 매핑.
# 2026-07-19 라이브 검증: 11종 모두 print/electronic ISSN이 /journals/{issn}
# 및 /journals/{issn}/works 에서 동일한 결과(같은 total-results, 같은 items)를
# 반환했다. 관례상 electronic ISSN을 채택(양쪽 다 실사용 가능함을 확인했으므로
# 필요 시 아래 값을 print ISSN으로 바꿔도 무방).
RSC_JOURNAL_ISSN = {
    "Journal of Materials Chemistry A": "2050-7496",       # print 2050-7488
    "Journal of Materials Chemistry B": "2050-7518",       # print 2050-750X
    "Journal of Materials Chemistry C": "2050-7534",       # print 2050-7526
    "Energy & Environmental Science": "1754-5706",         # print 1754-5692
    "Catalysis Science & Technology": "2044-4761",         # print 2044-4753
    "Green Chemistry": "1463-9270",                        # print 1463-9262
    "Chemical Science": "2041-6539",                       # print 2041-6520
    "Dalton Transactions": "1477-9234",                    # print 1477-9226
    "Physical Chemistry Chemical Physics": "1463-9084",    # print 1463-9076
    "Nanoscale": "2040-3372",                              # print 2040-3364
    "Chemical Society Reviews": "1460-4744",                # print 0306-0012
}

# 공백기 시작(6월 중순 ToC 누락)을 넉넉히 덮도록 6/1부터 조회.
# 종료일은 두지 않는다 - 정상화 이후(7/14~) 분이 섞여도 캐시/Notion 이중
# 안전망이 이미 저장된 건은 걸러주므로 안전하다.
BACKFILL_FROM_DATE = "2026-06-01"

CROSSREF_BASE_URL = "https://api.crossref.org/journals"
CROSSREF_ROWS = 200
CROSSREF_USER_AGENT = "get-ASAP-backfill/1.0 (mailto:chan8374@gmail.com)"

# 서버 기준 상대경로 (analytics/notion_fetcher.py의 CACHE_DIR과 동일 위치)
_DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


# ---------- Crossref 수집 ----------

def _crossref_get(url: str, params: dict, max_retries: int = 3) -> dict:
    """Crossref API GET (네트워크 에러/429/5xx 시 지수 백오프 재시도)."""
    headers = {"User-Agent": CROSSREF_USER_AGENT}
    last_exc = None
    for attempt in range(max_retries + 1):
        try:
            resp = httpx.get(url, params=params, headers=headers, timeout=30)
        except (httpx.TimeoutException, httpx.NetworkError) as e:
            last_exc = e
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(
                    "Crossref 네트워크 에러(%s), %d초 후 재시도 (%d/%d)",
                    type(e).__name__, wait, attempt + 1, max_retries,
                )
                time.sleep(wait)
                continue
            raise
        if resp.status_code == 200:
            return resp.json()
        if resp.status_code in (429, 500, 502, 503, 504) and attempt < max_retries:
            wait = 2 ** attempt
            logger.warning(
                "Crossref %d 에러, %d초 후 재시도 (%d/%d)",
                resp.status_code, wait, attempt + 1, max_retries,
            )
            time.sleep(wait)
            continue
        resp.raise_for_status()
    if last_exc:
        raise last_exc
    raise RuntimeError("Crossref 요청 실패 (재시도 초과)")


def _fetch_journal_works(issn: str) -> list[dict]:
    """Crossref works API에서 커서 페이지네이션으로 저널의 전체 항목을 수집."""
    items: list[dict] = []
    cursor = "*"
    url = f"{CROSSREF_BASE_URL}/{issn}/works"

    while True:
        params = {
            "filter": f"type:journal-article,from-created-date:{BACKFILL_FROM_DATE}",
            "rows": CROSSREF_ROWS,
            "cursor": cursor,
        }
        message = _crossref_get(url, params).get("message", {})
        page_items = message.get("items", [])
        items.extend(page_items)

        next_cursor = message.get("next-cursor")
        if not page_items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return items


# ---------- 제목/날짜 매핑 ----------

def _clean_title(raw_title: str) -> str:
    """Crossref 제목의 HTML 태그(<sub>, <sup>, <i> 등)/엔티티를 제거하고 공백 정리.

    RSC Crossref 메타데이터는 화학식 첨자 등을 위해 제목에 HTML 태그를 그대로
    심어 보낸다(예: "MoS<sub>2</sub>"). parsers/*.py가 이메일 본문 HTML을
    다루는 방식(BeautifulSoup + 공백 정리)과 동일한 방식을 쓴다.
    """
    if not raw_title:
        return ""
    text = BeautifulSoup(raw_title, "lxml").get_text()
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _date_parts(item: dict, key: str) -> tuple[int, ...] | None:
    """item[key]["date-parts"][0]를 None이 섞이지 않은 튜플로 반환. 없으면 None."""
    date_parts = item.get(key, {}).get("date-parts")
    if not date_parts or not date_parts[0] or date_parts[0][0] is None:
        return None
    return tuple(p for p in date_parts[0] if p is not None)


def _parts_to_iso(parts: tuple[int, ...]) -> str:
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _resolve_published_date(item: dict) -> str | None:
    """published-print/published-online/created 중 실제 게시일을 판정(ISO).

    RSC의 Advance Article은 published-print가 아예 없고 published-online도
    연도만 기록된 경우가 대부분(실측: {"date-parts": [[2026]]}). 이런
    저정밀 값을 1월 1일로 환산해 그대로 최솟값 비교에 넣으면, 실제로는
    6~7월에 나온 논문들이 무더기로 1월 1일에 찍히는 '가짜 스파이크'가 생겨
    이 백필의 핵심 목적(수집일 가짜 스파이크 방지)에 정면으로 반한다.
    따라서 연-월-일 3요소를 모두 갖춘(day 단위 정밀도) 필드만 후보로 삼아
    그중 가장 이른 날짜를 쓰고, day 단위 정밀도 필드가 하나도 없으면
    created로 폴백한다(created는 Crossref가 DOI를 등록한 시점이라 실측상
    거의 항상 day 단위 정밀도를 가지며, continuous publishing 저널에서는
    사실상 온라인 게시일과 같은 날 찍힌다).
    """
    day_precision_candidates = []
    for key in ("published-print", "published-online"):
        parts = _date_parts(item, key)
        if parts and len(parts) == 3:
            day_precision_candidates.append(_parts_to_iso(parts))
    if day_precision_candidates:
        return min(day_precision_candidates)

    created_parts = _date_parts(item, "created")
    if created_parts:
        return _parts_to_iso(created_parts)

    # 최후 폴백: day 정밀도가 전혀 없어도 있는 값이라도 사용(완전 누락보다 낫다)
    for key in ("published-online", "published-print"):
        parts = _date_parts(item, key)
        if parts:
            return _parts_to_iso(parts)
    return None


def _map_item(item: dict, journal_name: str) -> PaperMetadata | None:
    """Crossref work item -> PaperMetadata. 매핑 불가(제목/날짜 없음) 시 None."""
    titles = item.get("title") or []
    raw_title = titles[0] if titles else ""
    title = _clean_title(raw_title)
    if not title:
        return None

    date_str = _resolve_published_date(item)
    if not date_str:
        logger.warning(
            "게시일 확인 불가로 스킵: %s (DOI=%s)", title[:80], item.get("DOI"),
        )
        return None

    doi = item.get("DOI", "")
    url = f"https://doi.org/{doi}" if doi else ""

    # journal은 Crossref container-title이 아니라 publishers.json의 정식 명칭을 그대로 사용
    # (표기 흔들림 방지 - 매개변수로 받은 journal_name이 곧 그 정식 명칭)
    return PaperMetadata(title=title, journal=journal_name, date=date_str, url=url)


def _collect_journal_papers(journal_name: str, issn: str) -> list[PaperMetadata]:
    """한 저널의 Crossref 항목을 수집 -> 매핑 -> 필터링(main.py/filters.py 기준)."""
    logger.info("Crossref 조회 시작: %s (ISSN=%s)", journal_name, issn)
    try:
        items = _fetch_journal_works(issn)
    except httpx.HTTPError as e:
        logger.warning("Crossref 조회 실패 (journal=%s): %s", journal_name, e)
        return []
    logger.info("Crossref 수집 %d건: %s", len(items), journal_name)

    papers: list[PaperMetadata] = []
    for item in items:
        paper = _map_item(item, journal_name)
        if paper is None:
            continue
        if not is_valid_paper_title(paper.title):
            logger.debug("비논문 제목 스킵: %s", paper.title[:80])
            continue
        if _is_excluded_journal(paper.journal):
            logger.info("제외 저널 스킵 (journal=%s): %s", paper.journal, paper.title[:80])
            continue
        papers.append(paper)

    return papers


# ---------- 캐시 CSV 선(先)중복 제거 ----------

def _load_cache_titles(cache_dir: str, months: set[str]) -> tuple[set[str], set[str]]:
    """cache/papers_YYYY-MM.csv에서 정규화된 제목 집합을 로드.

    notion_client_mod.save_papers가 쓰는 것과 동일한 정규화
    (_normalize_title_for_duplicate)를 써야 이중 안전망이 같은 잣대를 쓴다.

    Returns:
        (정규화된 제목 집합, 캐시 파일이 없어 중복 제거를 생략한 월 집합)
    """
    titles: set[str] = set()
    missing_months: set[str] = set()

    for month in sorted(months):
        path = os.path.join(cache_dir, f"papers_{month}.csv")
        if not os.path.exists(path):
            missing_months.add(month)
            continue
        with open(path, "r", encoding="utf-8-sig", newline="") as f:
            reader = csv.DictReader(f)
            for row in reader:
                title = (row.get("title") or "").strip()
                if title:
                    titles.add(_normalize_title_for_duplicate(title))
        logger.info("캐시 로드: %s", path)

    return titles, missing_months


# ---------- 리포트 출력 ----------

def _print_dry_run_report(
    stats: dict[str, dict],
    to_add: list[PaperMetadata],
    missing_cache_months: set[str],
) -> None:
    print("\n=== RSC 공백기 백필 리포트 ===")
    print(f"조회 기간: {BACKFILL_FROM_DATE} ~ (created-date 기준)")

    if missing_cache_months:
        print(
            f"[경고] 캐시 파일 없음 - 중복 제거 생략: "
            f"{', '.join(sorted(missing_cache_months))}"
        )
    else:
        print("캐시 파일 정상 로드됨")

    print()
    col1, col2, col3, col4 = 42, 14, 16, 10
    header = (
        f"{'저널':<{col1}} {'Crossref 수집':>{col2}} "
        f"{'캐시 중복 제거':>{col3}} {'추가 예정':>{col4}}"
    )
    print(header)
    print("-" * len(header))

    total_collected = total_cache_dup = total_to_add = 0
    for journal_name, s in stats.items():
        print(
            f"{journal_name:<{col1}} {s['collected']:>{col2}} "
            f"{s['cache_dup']:>{col3}} {s['to_add']:>{col4}}"
        )
        total_collected += s["collected"]
        total_cache_dup += s["cache_dup"]
        total_to_add += s["to_add"]
    print("-" * len(header))
    print(
        f"{'합계':<{col1}} {total_collected:>{col2}} "
        f"{total_cache_dup:>{col3}} {total_to_add:>{col4}}"
    )

    print("\n=== 저널별 샘플 제목 (추가 예정 중 최대 2개) ===")
    samples_by_journal: dict[str, list[PaperMetadata]] = {}
    for p in to_add:
        samples_by_journal.setdefault(p.journal, []).append(p)
    for journal_name in stats:
        samples = samples_by_journal.get(journal_name, [])[:2]
        print(f"\n[{journal_name}]")
        if not samples:
            print("  (추가 예정 없음)")
        for p in samples:
            print(f"  - {p.date}  {p.title}")

    print("\n=== 날짜 분포 (추가 예정 기준, 월별 건수) ===")
    month_counts: dict[str, int] = {}
    for p in to_add:
        month = p.date[:7]
        month_counts[month] = month_counts.get(month, 0) + 1
    if not month_counts:
        print("  (추가 예정 없음)")
    for month in sorted(month_counts):
        print(f"  {month}: {month_counts[month]}건")

    print(f"\n총 추가 예정: {total_to_add}건")
    print("(--execute 없이 실행되면 저장되지 않습니다)")


# ---------- Notion 저장 ----------

def _execute_save(to_add: list[PaperMetadata]) -> None:
    if not to_add:
        logger.info("추가할 논문이 없어 저장을 건너뜁니다.")
        return

    db_id = get_or_create_db()
    logger.info("Notion DB 저장 시작: %d건 (db_id=%s)", len(to_add), db_id)
    # save_papers 내부에서 "저장 중: n/N - 제목" 진행 로그를 남기고,
    # 배치 내 중복(_normalize_title_for_duplicate) + DB 중복(_is_duplicate)
    # 이중 안전망을 그대로 통과한다.
    result = save_papers(to_add, db_id)
    logger.info(
        "저장 완료: %d건 저장, %d건 중복 스킵, %d건 실패",
        result.get("saved", 0), result.get("skipped", 0), result.get("failed", 0),
    )

    if result.get("saved", 0) > 0:
        _refresh_cache_from_notion()


# ---------- 로깅/인자 ----------

def _setup_logging(verbose: bool) -> None:
    """main.py 스타일 콘솔 로깅. 운영 파이프라인의 logs/get-asap.log와는 분리된
    일회성 실행이므로 파일 핸들러 없이 콘솔 출력만 구성한다."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stdout,
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="RSC 저널 공백기(2026-06 중순~07-14) Crossref 백필 스크립트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        default=False,
        help="실제 Notion 저장 + 캐시 재생성 실행 (기본값은 드라이런)",
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
        help="선(先)중복 제거에 쓸 캐시 CSV 디렉터리 (기본: 서버 기준 ./cache)",
    )
    return parser.parse_args(argv)


# ---------- 진입점 ----------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    logger.info(
        "RSC 공백기 백필 시작 (기간: %s~, execute=%s)",
        BACKFILL_FROM_DATE, args.execute,
    )

    stats: dict[str, dict] = {}
    candidates: list[PaperMetadata] = []
    for journal_name, issn in RSC_JOURNAL_ISSN.items():
        papers = _collect_journal_papers(journal_name, issn)
        stats[journal_name] = {"collected": len(papers), "cache_dup": 0, "to_add": 0}
        candidates.extend(papers)

    logger.info("전체 Crossref 수집(필터 통과) %d건", len(candidates))

    months = {p.date[:7] for p in candidates if p.date}
    cache_titles, missing_months = _load_cache_titles(args.cache_dir, months)
    if missing_months:
        logger.warning(
            "캐시 파일 없음, 해당 월은 중복 제거 생략: %s",
            ", ".join(sorted(missing_months)),
        )

    to_add: list[PaperMetadata] = []
    for paper in candidates:
        key = _normalize_title_for_duplicate(paper.title)
        if key in cache_titles:
            stats[paper.journal]["cache_dup"] += 1
            continue
        stats[paper.journal]["to_add"] += 1
        to_add.append(paper)

    _print_dry_run_report(stats, to_add, missing_months)

    if args.execute:
        _execute_save(to_add)
    else:
        logger.info("드라이런 완료. 실제 저장은 --execute 플래그로 실행하세요.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
