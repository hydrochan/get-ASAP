#!/usr/bin/env python
"""Notion 월별 DB를 읽어 cache/papers_YYYY-MM.csv 재생성.
cron으로 2시간마다 호출 (main.py cron 사이 간격 메꿈).
"""
import argparse
import os
from datetime import date

from analytics.notion_fetcher import fetch_papers, recent_month_range


def _default_lookback_months() -> int:
    raw = os.getenv("CACHE_REFRESH_LOOKBACK_MONTHS", "1")
    try:
        return max(0, int(raw))
    except ValueError:
        return 1


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Refresh get-ASAP Notion CSV cache",
    )
    parser.add_argument(
        "--month",
        help="단일 월만 강제 갱신 (YYYY-MM)",
    )
    parser.add_argument(
        "--start",
        help="갱신 시작 월 (YYYY-MM)",
    )
    parser.add_argument(
        "--end",
        help="갱신 종료 월 (YYYY-MM)",
    )
    parser.add_argument(
        "--lookback-months",
        type=int,
        default=None,
        help="기본 갱신 범위: N개월 전부터 현재 월까지",
    )
    return parser.parse_args()


if __name__ == '__main__':
    args = _parse_args()
    if args.month:
        start = end = args.month
    elif args.start or args.end:
        if not args.start or not args.end:
            raise SystemExit("--start와 --end는 같이 지정해야 합니다.")
        start, end = args.start, args.end
    else:
        lookback_months = (
            args.lookback_months
            if args.lookback_months is not None
            else _default_lookback_months()
        )
        start, end = recent_month_range(date.today(), lookback_months)

    df = fetch_papers(start, end, force_refresh=True)
    print(f'{start}~{end}: {len(df)} papers')
