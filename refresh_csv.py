#!/usr/bin/env python
"""현재 월 Notion DB를 읽어 cache/papers_YYYY-MM.csv 재생성.
cron으로 2시간마다 호출 (main.py cron 사이 간격 메꿈).
"""
from datetime import date
from analytics.notion_fetcher import fetch_papers

if __name__ == '__main__':
    m = date.today().strftime('%Y-%m')
    df = fetch_papers(m, m, force_refresh=True)
    print(f'{m}: {len(df)} papers')
