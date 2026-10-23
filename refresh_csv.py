#!/usr/bin/env python
"""정본 SQLite(data/asap.db)에서 cache/papers_YYYY-MM.csv를 export.

기본 동작: 마지막 export 이후 변경된 월 bucket만 다시 쓴다(max(change_seq) > meta csv_seq:<월>).
바뀐 것이 없으면 아무것도 하지 않으므로 cron으로 15분 간격 실행해도 부담이 없다.
(paper_autodown의 판정이 SQLite에 반영되면 다음 실행에서 해당 월 CSV가 갱신된다.)

  python refresh_csv.py                      # 변경된 월만
  python refresh_csv.py --all                # 모든 월 강제 (제외 저널 목록을 바꾼 뒤 등)
  python refresh_csv.py --month 2026-09      # 단일 월 강제
  python refresh_csv.py --start 2026-04 --end 2026-09   # 범위 강제
"""
import argparse
import logging
import sys

import store


def _months_between(start: str, end: str) -> list[str]:
    months = []
    year, month = int(start[:4]), int(start[5:7])
    end_key = (int(end[:4]), int(end[5:7]))
    while (year, month) <= end_key:
        months.append(f"{year:04d}-{month:02d}")
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return months


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export get-ASAP SQLite -> CSV cache")
    parser.add_argument("--all", action="store_true", help="모든 월 bucket 강제 export")
    parser.add_argument("--month", help="단일 월만 강제 export (YYYY-MM)")
    parser.add_argument("--start", help="강제 export 시작 월 (YYYY-MM)")
    parser.add_argument("--end", help="강제 export 종료 월 (YYYY-MM)")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if bool(args.start) != bool(args.end):
        print("--start와 --end는 같이 지정해야 합니다.", file=sys.stderr)
        return 2
    try:
        conn = store.connect()
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        if args.all:
            done = store.export_all(conn)
        elif args.month or args.start:
            months = [args.month] if args.month else _months_between(args.start, args.end)
            done = {m: store.export_csv(m, conn) for m in months}
        else:
            done = store.export_dirty(conn)
    finally:
        conn.close()
    if done:
        print(", ".join(f"{m}: {n} papers" for m, n in sorted(done.items())))
    else:
        print("변경 없음 - export 생략")
    return 0


if __name__ == "__main__":
    sys.exit(main())
