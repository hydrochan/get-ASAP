"""ACS 저널 공백기 백필 스크립트 (일회성/반복 운영 스크립트, backfill_rsc.py와 같은 성격).

배경:
  ACS가 2026-07-21 이후 ToC 알림 메일을 거의 보내지 않아 약 2주치(추정
  1,500~1,800편)가 수집 누락됐다. 메일 자체가 없으므로 Gmail 경로로는 복구
  불가 -> backfill_rsc.py(2026-07 RSC 공백기 백필 선례)와 동일한 방식으로
  Crossref works API에서 직접 수집해 Notion에 백필한다.

RSC 백필과의 차이점:
  1. 기간이 인자화되어 있다(--from/--to). ACS가 정상화될 때까지 이 스크립트를
     매주 재실행할 계획이므로 날짜를 하드코딩하지 않는다.
  2. 논문 date의 월(YYYY-MM)별로 그룹지어 해당 월의 Notion DB에 직접
     저장한다. notion_client_mod.get_or_create_db()는 date.today() 기준
     "현재월" DB로 고정되므로, 그대로 쓰면(오늘은 8월) 7월 논문이 8월 DB로
     잘못 들어간다 - 6월 RSC 백필 때 relocate_june_backfill.py로 사후
     정정해야 했던 바로 그 사고를 이번엔 사후 이사 없이 처음부터 피한다.

사용법:
  python backfill_acs.py                              # 드라이런: 2026-07-20~오늘
  python backfill_acs.py --from 2026-07-20 --to 2026-07-31
  python backfill_acs.py --execute                     # 실제 Notion 저장 + 캐시 재생성
  python backfill_acs.py --verbose

주의:
  state.json, Gmail 라벨, cache/announcements.json은 이 스크립트가 절대
  건드리지 않는다(Gmail 경로와 무관하게 Crossref로만 수집하므로 증분 동기화
  상태와 엮일 이유가 없다).
"""
import argparse
import logging
import os
import sys
from datetime import datetime

import httpx

import config
from backfill_rsc import _crossref_get, _load_cache_titles, _map_item
from main import _is_excluded_journal, _refresh_cache_from_notion
from models import PaperMetadata
from notion_client_mod import (
    _find_monthly_db,
    _normalize_title_for_duplicate,
    create_paper_db,
    save_papers,
)
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


# ---------- 상수 ----------

# publishers.json의 acs.journals(2026-08-04 라이브 Crossref 검증) -> ISSN 매핑.
# 검증 방법: /journals?query=<이름> 으로 후보 조회 -> 제목이 정확히 일치하는
# 항목만 채택 -> /journals/{issn} 로 제목 재확인 -> /journals/{issn}/works
# 로 2026-07-01 이후 실제 논문이 존재하는지 확인. 4단계 모두 통과한 항목만
# 아래에 있다(추측으로 채운 값 없음).
#
# 주의:
# - Crossref journals 검색 엔드포인트는 쿼리에 리터럴 "&"가 들어가면(URL
#   인코딩되어도) 0건을 반환한다(실측). "&"가 포함된 저널은 접속사를 뺀
#   쿼리("Energy Fuels" 등)로 검색해 우회했다 - 저장값(딕셔너리 키)은 항상
#   정식 표기("Energy & Fuels")를 그대로 쓴다.
# - "JACS"와 "Journal of the American Chemical Society"는 같은 저널이라
#   중복 조회하지 않고 실제 라이브 CSV(2026-07)에서 쓰인 표기인 후자
#   하나로만 등록한다(publishers.json에는 둘 다 있음).
# - "ACS Applied Materials"(Crossref 정식명 "ACS Applied Materials & Interfaces",
#   ISSN 1944-8252/1944-8244)는 2026-08-04 라이브 검증까지는 통과했으나
#   2026-08-05 사용자 지시로 이번 백필 대상에서 제외했다: 2026-04~08 라이브
#   CSV 전수 확인 결과 이 저널이 기존 원장에 한 번도 쓰인 적이 없어(=애초에
#   알림 구독 자체가 없었던 것으로 판단), ACS 공백기 복구와는 무관한 신규
#   저널 편입으로 보고 이번 백필 범위에서 뺐다. excluded_journals.json에는
#   올리지 않는다 - 향후 이 저널을 정식 구독하면 평소 Gmail 파이프라인으로
#   정상 수집되어야 하므로 서빙 차단 목록에 넣을 이유가 없다. ISSN
#   1944-8252는 계속 여기 주석으로만 남겨둔다(재편입 시 재검증 없이 바로 사용).
ACS_JOURNAL_ISSN = {
    "Journal of the American Chemical Society": "1520-5126",  # print 0002-7863
    "JACS Au": "2691-3704",
    "ACS Nano": "1936-086X",                                   # print 1936-0851
    "ACS Catalysis": "2155-5435",
    "Nano Letters": "1530-6992",                                # print 1530-6984
    "ACS Applied Nano Materials": "2574-0970",
    "ACS Applied Energy Materials": "2574-0962",
    "ACS Applied Engineering Materials": "2771-9545",
    "ACS Applied Optical Materials": "2771-9855",
    # "ACS Applied Materials": "1944-8252",  # 2026-08-05 사용자 지시로 제외(위 주석 참고)
    "ACS Central Science": "2374-7951",                         # print 2374-7943
    "ACS Energy Letters": "2380-8195",
    "ACS Engineering Au": "2694-2488",
    "ACS Materials Au": "2694-2461",
    "ACS Materials Letters": "2639-4979",
    "ACS Measurement Science Au": "2694-250X",
    "ACS Nanoscience Au": "2694-2496",
    "ACS Omega": "2470-1343",
    "ACS Organic & Inorganic Au": "2694-247X",
    "ACS Photonics": "2330-4022",
    "ACS Physical Chemistry Au": "2694-2445",
    "ACS Sustainable Chemistry & Engineering": "2168-0485",
    "ACS Sustainable Resource Management": "2837-1445",
    "Accounts of Materials Research": "2643-6728",
    "Analytical Chemistry": "1520-6882",                        # print 0003-2700
    "Chemical Reviews": "1520-6890",                            # print 0009-2665
    "Chemistry of Materials": "1520-5002",                      # print 0897-4756
    "Energy & Fuels": "1520-5029",                              # print 0887-0624
    "Environmental Science & Technology": "1520-5851",          # print 0013-936X
    "Environmental Science & Technology Letters": "2328-8930",
    "Industrial & Engineering Chemistry Research": "1520-5045", # print 0888-5885
    "Journal of Chemical & Engineering Data": "1520-5134",      # print 0021-9568
    "Langmuir": "1520-5827",                                    # print 0743-7463
    "The Journal of Physical Chemistry C": "1932-7455",         # print 1932-7447
    "The Journal of Physical Chemistry Letters": "1948-7185",
}

DEFAULT_FROM_DATE = "2026-07-20"

CROSSREF_BASE_URL = "https://api.crossref.org/journals"
CROSSREF_ROWS = 200

# 서버 기준 상대경로 (analytics/notion_fetcher.py의 CACHE_DIR과 동일 위치)
_DEFAULT_CACHE_DIR = os.path.join(os.path.dirname(__file__), "cache")


# ---------- Crossref 수집 ----------

def _fetch_journal_works(issn: str, from_date: str, to_date: str | None) -> list[dict]:
    """Crossref works API에서 커서 페이지네이션으로 [from_date, to_date] 구간 전체 항목을 수집.

    backfill_rsc._fetch_journal_works와 로직은 동일하지만, 그쪽은 날짜를
    모듈 상수(BACKFILL_FROM_DATE)로 고정해 재사용할 수 없다 - 이 스크립트는
    --from/--to로 매주 다른 기간을 조회해야 하므로 날짜를 인자로 받는
    버전을 별도로 둔다.
    """
    items: list[dict] = []
    cursor = "*"
    url = f"{CROSSREF_BASE_URL}/{issn}/works"

    filter_parts = ["type:journal-article", f"from-created-date:{from_date}"]
    if to_date:
        filter_parts.append(f"until-created-date:{to_date}")
    filter_str = ",".join(filter_parts)

    while True:
        params = {"filter": filter_str, "rows": CROSSREF_ROWS, "cursor": cursor}
        message = _crossref_get(url, params).get("message", {})
        page_items = message.get("items", [])
        items.extend(page_items)

        next_cursor = message.get("next-cursor")
        if not page_items or not next_cursor or next_cursor == cursor:
            break
        cursor = next_cursor

    return items


def _collect_journal_papers(
    journal_name: str, issn: str, from_date: str, to_date: str | None
) -> tuple[list[PaperMetadata], dict]:
    """한 저널의 Crossref 항목을 수집 -> 매핑 -> 필터링(main.py/filters.py 기준).

    Returns:
        (필터 통과 논문 목록, {"raw": Crossref 원본 건수,
         "unmapped_or_non_article": 매핑 실패(제목/날짜 없음) + 비논문 제목 필터
         제외 건수, "excluded_journal": 제외 저널 필터 제외 건수})
    """
    logger.info("Crossref 조회 시작: %s (ISSN=%s)", journal_name, issn)
    try:
        items = _fetch_journal_works(issn, from_date, to_date)
    except httpx.HTTPError as e:
        logger.warning("Crossref 조회 실패 (journal=%s): %s", journal_name, e)
        return [], {"raw": 0, "unmapped_or_non_article": 0, "excluded_journal": 0}
    logger.info("Crossref 수집 %d건: %s", len(items), journal_name)

    papers: list[PaperMetadata] = []
    unmapped_or_non_article = 0
    excluded_journal = 0
    for item in items:
        paper = _map_item(item, journal_name)
        if paper is None:
            unmapped_or_non_article += 1
            continue
        if not is_valid_paper_title(paper.title):
            logger.debug("비논문 제목 스킵: %s", paper.title[:80])
            unmapped_or_non_article += 1
            continue
        if _is_excluded_journal(paper.journal):
            logger.info("제외 저널 스킵 (journal=%s): %s", paper.journal, paper.title[:80])
            excluded_journal += 1
            continue
        papers.append(paper)

    stats = {
        "raw": len(items),
        "unmapped_or_non_article": unmapped_or_non_article,
        "excluded_journal": excluded_journal,
    }
    return papers, stats


# ---------- 월별 Notion DB 획득/생성 ----------

def _get_or_create_monthly_db(month_str: str) -> str:
    """지정 월("YYYY-MM")의 Notion DB를 찾아 반환, 없으면 생성.

    notion_client_mod.get_or_create_db()는 date.today() 기준 "현재월" DB만
    반환하므로 이 백필처럼 과거/미래 월 논문을 저장할 때는 쓸 수 없다.
    """
    if not config.NOTION_PARENT_PAGE_ID:
        raise ValueError(
            "NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. .env 파일에 설정하세요."
        )

    db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, month_str)
    if db_id:
        logger.info("기존 월별 DB 사용: get-ASAP %s", month_str)
        return db_id

    logger.info("월별 DB 생성: get-ASAP %s", month_str)
    return create_paper_db(config.NOTION_PARENT_PAGE_ID, db_name=f"get-ASAP {month_str}")


# ---------- 리포트 출력 ----------

def _print_dry_run_report(
    stats: dict[str, dict],
    to_add: list[PaperMetadata],
    missing_cache_months: set[str],
    from_date: str,
    to_date: str | None,
) -> None:
    print("\n=== ACS 공백기 백필 리포트 ===")
    print(f"조회 기간: {from_date} ~ {to_date or '(오늘)'} (created-date 기준)")

    if missing_cache_months:
        print(
            f"[경고] 캐시 파일 없음 - 중복 제거 생략: "
            f"{', '.join(sorted(missing_cache_months))}"
        )
    else:
        print("캐시 파일 정상 로드됨")

    print()
    col1, col2, col3, col4, col5 = 44, 12, 12, 14, 10
    header = (
        f"{'저널':<{col1}} {'Crossref':>{col2}} {'비논문 제외':>{col3}} "
        f"{'캐시 중복':>{col4}} {'추가 예정':>{col5}}"
    )
    print(header)
    print("-" * len(header))

    total_raw = total_non_article = total_cache_dup = total_to_add = 0
    for journal_name, s in stats.items():
        print(
            f"{journal_name:<{col1}} {s['raw']:>{col2}} "
            f"{s['unmapped_or_non_article']:>{col3}} "
            f"{s['cache_dup']:>{col4}} {s['to_add']:>{col5}}"
        )
        total_raw += s["raw"]
        total_non_article += s["unmapped_or_non_article"]
        total_cache_dup += s["cache_dup"]
        total_to_add += s["to_add"]
    print("-" * len(header))
    print(
        f"{'합계':<{col1}} {total_raw:>{col2}} {total_non_article:>{col3}} "
        f"{total_cache_dup:>{col4}} {total_to_add:>{col5}}"
    )

    excluded_journal_total = sum(s["excluded_journal"] for s in stats.values())
    if excluded_journal_total:
        print(f"\n(참고: 제외 저널 필터로 추가 제외된 건수 {excluded_journal_total}건, 위 표에는 미포함)")

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

    print("\n=== 날짜 분포 (추가 예정 기준, 월별 건수 -> 저장될 월별 DB) ===")
    month_counts: dict[str, int] = {}
    for p in to_add:
        month = p.date[:7]
        month_counts[month] = month_counts.get(month, 0) + 1
    if not month_counts:
        print("  (추가 예정 없음)")
    for month in sorted(month_counts):
        print(f"  {month}: {month_counts[month]}건  ->  get-ASAP {month}")

    print(f"\n총 추가 예정: {total_to_add}건")
    print("(--execute 없이 실행되면 저장되지 않습니다)")


# ---------- Notion 저장 ----------

def _execute_save(to_add: list[PaperMetadata]) -> dict:
    """월별로 그룹지어 각 달의 Notion DB에 직접 저장."""
    if not to_add:
        logger.info("추가할 논문이 없어 저장을 건너뜁니다.")
        return {"saved": 0, "skipped": 0, "failed": 0}

    by_month: dict[str, list[PaperMetadata]] = {}
    for paper in to_add:
        by_month.setdefault(paper.date[:7], []).append(paper)

    total_saved = total_skipped = total_failed = 0
    for month_str in sorted(by_month):
        papers = by_month[month_str]
        db_id = _get_or_create_monthly_db(month_str)
        logger.info(
            "Notion DB 저장 시작: %d건 -> get-ASAP %s (db_id=%s)",
            len(papers), month_str, db_id,
        )
        result = save_papers(papers, db_id)
        logger.info(
            "저장 완료(get-ASAP %s): %d건 저장, %d건 중복 스킵, %d건 실패",
            month_str, result.get("saved", 0), result.get("skipped", 0), result.get("failed", 0),
        )
        total_saved += result.get("saved", 0)
        total_skipped += result.get("skipped", 0)
        total_failed += result.get("failed", 0)

    if total_saved > 0:
        _refresh_cache_from_notion()

    return {"saved": total_saved, "skipped": total_skipped, "failed": total_failed}


# ---------- 로깅/인자 ----------

def _setup_logging(verbose: bool) -> None:
    """backfill_rsc.py와 동일한 콘솔 로깅 스타일. 파일 핸들러 없이 콘솔 출력만 구성."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
        stream=sys.stdout,
    )


def _valid_date(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜는 YYYY-MM-DD 형식이어야 합니다: {value!r}")
    return value


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="ACS 저널 공백기(2026-07-21~) Crossref 백필 스크립트. "
        "정상화 전까지 매주 재실행 가능하도록 기간이 인자화되어 있다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--from",
        dest="from_date",
        type=_valid_date,
        default=DEFAULT_FROM_DATE,
        help=f"조회 시작일 YYYY-MM-DD (기본: {DEFAULT_FROM_DATE})",
    )
    parser.add_argument(
        "--to",
        dest="to_date",
        type=_valid_date,
        default=None,
        help="조회 종료일 YYYY-MM-DD (기본: 없음 = 오늘까지)",
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
    args = parser.parse_args(argv)

    if args.to_date and args.to_date < args.from_date:
        parser.error(f"--to({args.to_date})는 --from({args.from_date})보다 앞설 수 없습니다.")

    return args


# ---------- 진입점 ----------

def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    _setup_logging(args.verbose)

    logger.info(
        "ACS 공백기 백필 시작 (기간: %s~%s, execute=%s)",
        args.from_date, args.to_date or "(오늘)", args.execute,
    )

    stats: dict[str, dict] = {}
    candidates: list[PaperMetadata] = []
    for journal_name, issn in ACS_JOURNAL_ISSN.items():
        papers, jstats = _collect_journal_papers(journal_name, issn, args.from_date, args.to_date)
        stats[journal_name] = {**jstats, "cache_dup": 0, "to_add": 0}
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

    _print_dry_run_report(stats, to_add, missing_months, args.from_date, args.to_date)

    if args.execute:
        _execute_save(to_add)
    else:
        logger.info("드라이런 완료. 실제 저장은 --execute 플래그로 실행하세요.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
