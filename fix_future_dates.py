#!/usr/bin/env python
"""미래 발행일로 저장된 기존 행 정정 스크립트 (일회성 운영 스크립트, backup_db.py/purge_non_articles.py와 같은 성격).

배경:
  출판사가 발행일을 수신일보다 미래로 기재하는 경우가 있다(예: ACS가 JACS 논문을 2026-12-31로 기재).
  main.py는 이제 이런 논문의 발행일을 메일 수신일로 대체해 저장하지만(_effective_pub_date), 그 전에 저장된 행은
  그대로 남아 대시보드 추이를 왜곡한다. 이 스크립트가 그 행들을 정정한다.

대상:
  정본 행(deleted_at IS NULL AND dup_of IS NULL) 중 pub_date > substr(created_at, 1, 10) 인 행.
  메일 수신일은 저장되지 않으므로 수집 시각(created_at, UTC)의 날짜를 가장 가까운 대용값으로 쓴다
  (수집이 6시간마다 돌아 수신일과 같거나 하루 안쪽이다).

정정:
  pub_date = created_at의 날짜, bucket_month = store.bucket_month_for(새 pub_date, today=그 날짜).
  모든 변경은 store._update_paper를 거쳐 change_seq·updated_at이 함께 올라가므로
  (1) PC 동기화 피드 /api/sync/v1/changes 가 변경분을 받고 (2) export_dirty()가 해당 월 CSV를 다시 쓴다.
  행이 다른 월로 옮겨가면 옛 월 bucket은 max(change_seq)가 오르지 않아 dirty 로 잡히지 않으므로,
  옛 월의 csv_seq 메모를 지우고(다음 export_dirty 에서 재생성) 적용 직후 영향받은 월 CSV를 직접 다시 쓴다.

사용법 (VM의 프로젝트 루트에서):
  python fix_future_dates.py                  # 드라이런(기본): 대상 행만 출력, DB 변경 없음
  python fix_future_dates.py --apply          # 백업(backup_db) 후 한 트랜잭션으로 정정 + CSV 재생성
  python fix_future_dates.py --apply --skip-backup   # 방금 백업했다면 백업 생략

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다.
  재실행해도 안전하다(정정된 행은 더 이상 대상이 아니다).
"""
import argparse
import datetime
import logging
import sqlite3
import sys

import backup_db
import store

logger = logging.getLogger(__name__)

_SELECT_TARGETS = (
    "SELECT * FROM papers "
    "WHERE deleted_at IS NULL AND dup_of IS NULL "
    "AND pub_date IS NOT NULL AND pub_date > substr(created_at, 1, 10) "
    "ORDER BY id"
)


def _plan_row(row: sqlite3.Row) -> dict | None:
    """대상 행 한 건의 정정 계획. created_at에서 달력 날짜를 얻지 못하면 None(건드리지 않는다)."""
    new_pub = store.normalize_pub_date(row["created_at"])
    if new_pub is None:
        return None
    new_bucket = store.bucket_month_for(new_pub, today=datetime.date.fromisoformat(new_pub))
    if new_bucket < store.FIRST_BUCKET_MONTH:
        # 서비스 시작(첫 bucket) 이전 수집 시각이면 기존 bucket을 그대로 둔다(존재하지 않는 월 bucket 생성 방지)
        new_bucket = row["bucket_month"]
    return {
        "row": row,
        "id": row["id"],
        "title": row["title"],
        "journal": row["journal"],
        "old_pub": row["pub_date"],
        "new_pub": new_pub,
        "old_bucket": row["bucket_month"],
        "new_bucket": new_bucket,
    }


def find_targets(conn: sqlite3.Connection) -> list[dict]:
    """정정 대상 행의 계획 목록(읽기 전용)."""
    plans = []
    for row in conn.execute(_SELECT_TARGETS).fetchall():
        plan = _plan_row(row)
        if plan is None:
            logger.warning("created_at 을 해석할 수 없어 건너뜀 (id=%s): %r", row["id"], row["created_at"])
            continue
        plans.append(plan)
    return plans


def apply_fix(conn: sqlite3.Connection) -> list[dict]:
    """대상 행을 한 트랜잭션으로 정정하고 적용한 계획 목록을 반환한다.

    대상은 쓰기 잠금(BEGIN IMMEDIATE)을 잡은 뒤 다시 고른다(수집 cron과 겹쳐도 최신 상태 기준).
    중간에 예외가 나면 전부 롤백된다.
    """
    with store.write_tx(conn):
        plans = find_targets(conn)
        now = store._now()
        applied = []
        for plan in plans:
            changed = store._update_paper(
                conn, plan["row"], {"pub_date": plan["new_pub"]}, now=now, extra={"bucket_month": plan["new_bucket"]}
            )
            if changed:
                applied.append(plan)
        # 행이 떠난 월은 max(change_seq)가 오르지 않아 export_dirty 가 놓친다 -> csv_seq 메모를 지워 다음 export 에서 다시 쓰게 한다
        for month in {p["old_bucket"] for p in applied if p["old_bucket"] != p["new_bucket"]}:
            conn.execute("DELETE FROM meta WHERE key=?", (f"csv_seq:{month}",))
    return applied


def affected_months(plans: list[dict]) -> list[str]:
    """정정으로 CSV가 달라지는 월(옛 bucket ∪ 새 bucket), 오름차순."""
    return sorted({p["old_bucket"] for p in plans} | {p["new_bucket"] for p in plans})


def print_report(plans: list[dict], applied: bool) -> None:
    print(f"\n=== 미래 발행일 정정 대상 ({'적용 결과' if applied else '드라이런'}) ===")
    for p in plans:
        print(
            f"  id={p['id']}  {p['title'][:60]}  [{p['journal']}]\n"
            f"    pub_date: {p['old_pub']} -> {p['new_pub']}   bucket_month: {p['old_bucket']} -> {p['new_bucket']}"
        )
    print(f"\n총 {len(plans)}건, 영향받는 월: {', '.join(affected_months(plans)) or '(없음)'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="미래 발행일(pub_date > 수집일) 행을 수집일로 정정 (기본은 드라이런)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 정정 (기본은 드라이런)")
    parser.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        conn = store.connect()
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        plans = find_targets(conn)
        if not args.apply:
            print_report(plans, applied=False)
            logger.info("드라이런 완료. 실제 정정은 --apply 로 실행하세요.")
            return 0
        if not plans:
            logger.info("정정할 행이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        applied = apply_fix(conn)
        print_report(applied, applied=True)
        for month in affected_months(applied):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 정정은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("정정 완료: %d건", len(applied))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
