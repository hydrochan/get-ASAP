#!/usr/bin/env python
"""Nature 'Research' 섹션 누락분 백필 스크립트 (일회성 운영 스크립트, fix_future_dates.py와 같은 성격).

배경:
  parsers/nature.py가 평면 구조 저널(Nature Catalysis / Nature Energy / Nature Chemical Engineering 등)의
  'Research' 섹션과 'Review Articles' 섹션을 논문 섹션으로 보지 않아, 2026-04부터 이 저널들의 논문이
  거의 저장되지 않았다(Nature Catalysis는 0건). 파서를 고친 뒤에도 이미 라벨 처리된 옛 메일은 다시
  수집되지 않으므로, 이 스크립트가 Gmail의 Nature 발신 메일을 다시 읽어 빠진 논문만 채운다.

동작:
  1. publishers.json의 Nature 발신자 메일 중 --since 이후(수신일 UTC 기준) 메일을 Gmail에서 읽는다.
     Gmail은 list/get만 쓴다(라벨·읽음 상태 불변, 라벨 마킹 없음).
  2. 고친 파서로 본문을 파싱하고 main.run_pipeline과 같은 논문별 규칙(main._finalize_paper)을 적용한다:
     저널 추론·출판사 fallback 제외 / 제외 저널 제외 / 발행일 규칙(없거나 수신일보다 미래면 메일 수신일).
  3. 제목 키(store.normalize_title)로 DB와 대조해 '신규'인 논문만 고른다.

출력(드라이런, 기본):
  저널별 신규 건수 + 샘플 제목, 그리고 '오기 저널 의심 목록'.
  오기 저널 의심 = DB에서 journal이 'Nature'인데 제목이 Nature Methods 알림에 실린 행.
  (publishers.json에 Nature Methods가 없던 때 Methods 논문이 'Nature'로 저장됐다.)
  지금은 Nature Methods도 정상 수집 대상이라 그 알림의 논문은 'Nature Methods' 신규 후보가 되지만,
  오기 행은 제목 키가 이미 DB에 있어 신규 후보(중복 행)가 되지 않는다.
  이 목록은 보여주기만 한다. 삭제·저널명 정정은 하지 않는다(사용자가 따로 결정).

사용법 (VM의 프로젝트 루트에서):
  python backfill_nature_research.py                         # 드라이런(기본): DB를 읽기 전용으로 열고 변경 없음
  python backfill_nature_research.py --since 2026-04-01      # 조회 시작일(기본 2026-04-01)
  python backfill_nature_research.py --apply                 # 백업(backup_db) 후 신규 논문 저장 + 영향받은 월 CSV 재생성
  python backfill_nature_research.py --apply --skip-backup   # 방금 백업했다면 백업 생략

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다.
  재실행해도 안전하다(이미 저장된 제목은 신규가 아니고, store.save_papers도 중복을 건너뛴다).
  토큰이 만료돼 있으면 auth.get_gmail_service가 토큰 파일을 갱신한다(수집 cron과 같은 동작).
"""
import argparse
import datetime
import logging
import sys
from collections import Counter, defaultdict

import backup_db
import main as pipeline
import store
from auth import get_gmail_service
from gmail_client import extract_body, infer_journal, is_message_not_found
from parsers.nature import NatureParser

logger = logging.getLogger(__name__)

NATURE_KEY = "nature"  # publishers.json의 Nature 출판사 key
DEFAULT_SINCE = "2026-04-01"
SAMPLES_PER_JOURNAL = 3
METHODS_JOURNAL = "Nature Methods"
MAIN_JOURNAL = "Nature"


def nature_query(publishers: dict, since: datetime.date) -> str:
    """Nature 발신자 + since(UTC 자정) 이후 메일을 찾는 Gmail 검색식.

    main의 build_query와 달리 `-label:get-ASAP-processed`를 붙이지 않는다(대상이 전부 처리 완료 라벨 메일이다).
    """
    senders = publishers[NATURE_KEY]["sender"]
    if isinstance(senders, str):
        senders = [senders]
    after = int(datetime.datetime.combine(since, datetime.time.min, tzinfo=datetime.timezone.utc).timestamp())
    return f"({' OR '.join(f'from:{s}' for s in senders)}) after:{after}"


def list_message_ids(service, query: str) -> list[str]:
    """검색식에 맞는 메일 ID 전체(모든 페이지). 스팸함도 포함한다(수집 경로와 같다)."""
    ids, page_token = [], None
    while True:
        kwargs = {"userId": "me", "q": query, "includeSpamTrash": True, "maxResults": 500}
        if page_token:
            kwargs["pageToken"] = page_token
        response = service.users().messages().list(**kwargs).execute()
        ids.extend(m["id"] for m in response.get("messages", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            return ids


def collect(service, publishers: dict, since: datetime.date, conn) -> dict:
    """Gmail을 읽어 신규 논문 후보와 오기 저널 의심 행을 계산한다(DB·Gmail 모두 읽기만).

    반환: stats(Counter), new(list[(msg_id, PaperMetadata)] 메일 순서), misattributed(list[sqlite3.Row]).
    """
    parser = NatureParser()
    stats = Counter()
    existing = {r[0] for r in conn.execute("SELECT title_key FROM papers")}
    new: dict[str, tuple[str, object]] = {}  # title_key -> (msg_id, paper), 첫 등장만 유지
    methods_keys: set[str] = set()  # Nature Methods 알림에 실린 제목 키
    main_keys: set[str] = set()  # 'Nature' 본지 알림에 실린 제목 키(진짜 Nature 논문)
    today = datetime.date.today().isoformat()

    for msg_id in list_message_ids(service, nature_query(publishers, since)):
        try:
            msg = service.users().messages().get(userId="me", id=msg_id, format="full").execute()
        except Exception as e:
            if is_message_not_found(e):
                logger.warning("삭제된 메일(404 notFound), 건너뜀: %s", msg_id)
                stats["gone"] += 1
            else:
                logger.warning("메일 %s 읽기 실패, 건너뜀: %s", msg_id, e)
                stats["failed"] += 1
            continue

        mail_date = pipeline._mail_received_date(msg, today)
        if mail_date < since.isoformat():
            continue
        headers = msg["payload"].get("headers", [])
        sender = pipeline._extract_header(headers, "From")
        subject = pipeline._extract_header(headers, "Subject")
        if pipeline._find_publisher_key(sender, publishers) != NATURE_KEY:
            continue
        stats["messages"] += 1

        try:
            papers = parser.parse(extract_body(msg["payload"]))
        except Exception as e:
            logger.warning("메일 %s 파싱 실패, 건너뜀: %s", msg_id, e)
            stats["failed"] += 1
            continue
        stats["extracted"] += len(papers)

        alert_journal = infer_journal(sender, subject, publishers)
        for paper in papers:
            key = store.normalize_title(paper.title)
            if alert_journal == METHODS_JOURNAL:
                methods_keys.add(key)
            elif alert_journal == MAIN_JOURNAL:
                main_keys.add(key)
            final = pipeline._finalize_paper(
                paper, sender=sender, subject=subject, publishers=publishers,
                pub_key=NATURE_KEY, mail_date=mail_date,
            )
            if final is None:
                stats["skipped_by_rules"] += 1
            elif key in existing:
                stats["already_in_db"] += 1
            elif key in new:
                stats["duplicate_in_mails"] += 1
            else:
                new[key] = (msg_id, final)

    misattributed = [
        r for r in conn.execute(
            "SELECT id, title, title_key, pub_date FROM papers "
            "WHERE journal=? AND deleted_at IS NULL AND dup_of IS NULL ORDER BY id",
            (MAIN_JOURNAL,),
        )
        if r["title_key"] in methods_keys and r["title_key"] not in main_keys
    ]
    return {"stats": stats, "new": list(new.values()), "misattributed": misattributed}


def affected_months(new: list) -> list[str]:
    """신규 논문이 들어갈 월 bucket(store.save_papers와 같은 규칙), 오름차순."""
    return sorted({store.bucket_month_for(paper.date) for _, paper in new})


def apply_new(conn, new: list) -> dict:
    """신규 논문을 메일 단위로(main과 같은 방식: 메일 1통 = 한 트랜잭션) store.save_papers로 저장한다."""
    by_message = defaultdict(list)
    for msg_id, paper in new:
        by_message[msg_id].append(paper)
    totals = Counter({"saved": 0, "skipped": 0, "failed": 0})
    for papers in by_message.values():
        totals.update(store.save_papers(papers, conn))
    return dict(totals)


def print_report(since: datetime.date, plan: dict, applied: dict | None) -> None:
    stats, new, mis = plan["stats"], plan["new"], plan["misattributed"]
    print(f"\n=== Nature 연구 섹션 백필 ({'적용 결과' if applied is not None else '드라이런'}) ===")
    print(
        f"조회: {since.isoformat()} 이후 Nature 메일 {stats['messages']}통"
        f" (삭제됨 {stats['gone']}, 읽기/파싱 실패 {stats['failed']})"
    )
    print(
        f"파서 추출 {stats['extracted']}건 -> 규칙 제외 {stats['skipped_by_rules']}"
        f" (제외 저널·저널 추론 실패), 이미 DB에 있음 {stats['already_in_db']},"
        f" 메일 간 중복 {stats['duplicate_in_mails']}"
    )

    by_journal = defaultdict(list)
    for _, paper in new:
        by_journal[paper.journal].append(paper)
    print(f"\n신규 논문 {len(new)}건 - 저널별:")
    for journal, papers in sorted(by_journal.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        print(f"  {journal}: {len(papers)}건")
    print("\n샘플 (저널별 최대 %d건):" % SAMPLES_PER_JOURNAL)
    for journal, papers in sorted(by_journal.items()):
        for paper in papers[:SAMPLES_PER_JOURNAL]:
            print(f"  [{journal}] {paper.title[:80]}  ({paper.date})")

    print(f"\n오기 저널 의심 {len(mis)}건 (DB journal='Nature'인데 Nature Methods 알림에 실린 제목, 이 스크립트는 건드리지 않음):")
    for r in mis:
        print(f"  id={r['id']}  {r['title'][:70]}  pub_date={r['pub_date']}")

    print(f"\n영향받는 월: {', '.join(affected_months(new)) or '(없음)'}")
    if applied is not None:
        print(f"저장 결과: 저장 {applied['saved']}건, 중복 스킵 {applied['skipped']}건, 실패 {applied['failed']}건")


def _since_date(value: str) -> datetime.date:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"YYYY-MM-DD 형식이어야 합니다: {value!r}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Nature Research/Review Articles 섹션 누락 논문 백필 (기본은 드라이런)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--since", type=_since_date, default=_since_date(DEFAULT_SINCE),
        help=f"이 날짜(UTC 수신일) 이후 메일만 대상 (기본 {DEFAULT_SINCE})",
    )
    parser.add_argument("--apply", action="store_true", help="실제로 신규 논문을 저장 (기본은 드라이런)")
    parser.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    # 논문별 규칙(main._finalize_paper)의 건별 INFO 로그(제외 저널 스킵 등)는 보고서에 집계되므로 숨긴다
    logging.getLogger(pipeline.__name__).setLevel(logging.WARNING)
    try:
        conn = store.connect() if args.apply else store.connect(readonly=True)
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        plan = collect(get_gmail_service(), pipeline._load_publishers(), args.since, conn)
        new, failed = plan["new"], plan["stats"]["failed"]
        if not args.apply:
            print_report(args.since, plan, applied=None)
            logger.info("드라이런 완료. 실제 저장은 --apply 로 실행하세요.")
            return 1 if failed else 0
        if not new:
            print_report(args.since, plan, applied={"saved": 0, "skipped": 0, "failed": 0})
            logger.info("저장할 신규 논문이 없어 종료합니다.")
            return 1 if failed else 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        applied = apply_new(conn, new)
        print_report(args.since, plan, applied=applied)
        for month in affected_months(new):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 저장은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("백필 완료: %d건 저장", applied["saved"])
        return 1 if failed or applied["failed"] else 0
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
