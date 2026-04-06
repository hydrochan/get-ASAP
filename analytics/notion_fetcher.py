"""Notion 월별 DB에서 논문 데이터를 fetch하여 pandas DataFrame으로 변환.

기존 notion_client_mod.py의 _find_monthly_db()를 재활용.
CSV 캐싱으로 반복 API 호출 방지.
"""
import logging
import os
from datetime import date

import pandas as pd

import config
from notion_auth import get_notion_client
from notion_client_mod import _find_monthly_db, _get_data_source_id

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")


def _parse_pages(pages: list[dict]) -> pd.DataFrame:
    """Notion API page 목록 → DataFrame 변환"""
    records = []
    for page in pages:
        props = page.get("properties", {})

        # Title
        title_arr = props.get("Title", {}).get("title", [])
        title = title_arr[0].get("plain_text", "") if title_arr else ""

        # Journal
        journal_sel = props.get("Journal", {}).get("select")
        journal = journal_sel.get("name", "") if journal_sel else ""

        # Date
        date_obj = props.get("Date", {}).get("date")
        date_str = date_obj.get("start", "") if date_obj else ""

        # URL
        url = props.get("URL", {}).get("url", "") or ""

        # Status
        status_sel = props.get("Status", {}).get("select")
        status = status_sel.get("name", "") if status_sel else ""

        records.append({
            "title": title,
            "journal": journal,
            "date": date_str,
            "url": url,
            "status": status,
        })

    df = pd.DataFrame(records, columns=["title", "journal", "date", "url", "status"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _generate_months(start: str, end: str) -> list[str]:
    """'YYYY-MM' 범위의 월 목록 생성 (inclusive)"""
    from datetime import datetime

    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")
    months = []
    current = s
    while current <= e:
        months.append(current.strftime("%Y-%m"))
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def find_monthly_dbs(parent_page_id: str, start: str, end: str) -> dict[str, str]:
    """기간 내 존재하는 월별 DB의 {month: db_id} 딕셔너리 반환"""
    months = _generate_months(start, end)
    result = {}
    for month in months:
        db_id = _find_monthly_db(parent_page_id, month)
        if db_id:
            result[month] = db_id
    return result


def _fetch_all_pages(database_id: str) -> list[dict]:
    """DB의 모든 페이지를 pagination하며 fetch"""
    client = get_notion_client()
    data_source_id = _get_data_source_id(database_id)

    all_pages = []
    cursor = None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.data_sources.query(data_source_id, **kwargs)
        all_pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return all_pages


def _save_cache(df: pd.DataFrame, month: str, cache_dir: str = None) -> None:
    """DataFrame을 CSV 캐시로 저장"""
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"papers_{month}.csv")
    df.to_csv(path, index=False)
    logger.info("캐시 저장: %s (%d건)", path, len(df))


def _load_cache(month: str, cache_dir: str = None) -> pd.DataFrame | None:
    """CSV 캐시에서 DataFrame 로드. 없으면 None."""
    cache_dir = cache_dir or CACHE_DIR
    path = os.path.join(cache_dir, f"papers_{month}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    logger.info("캐시 로드: %s (%d건)", path, len(df))
    return df


def fetch_papers(
    start: str, end: str, force_refresh: bool = False
) -> pd.DataFrame:
    """기간 내 논문 데이터를 fetch (캐시 우선, 현재 월은 항상 re-fetch).

    Args:
        start: 시작 월 (YYYY-MM)
        end: 종료 월 (YYYY-MM)
        force_refresh: True이면 캐시 무시하고 전부 re-fetch

    Returns:
        전체 기간 논문 DataFrame
    """
    parent_page_id = config.NOTION_PARENT_PAGE_ID
    if not parent_page_id:
        raise ValueError("NOTION_PARENT_PAGE_ID 환경변수가 필요합니다.")

    current_month = date.today().strftime("%Y-%m")
    db_map = find_monthly_dbs(parent_page_id, start, end)

    if not db_map:
        logger.warning("기간 %s ~ %s에 해당하는 DB가 없습니다.", start, end)
        return pd.DataFrame(columns=["title", "journal", "date", "url", "status"])

    frames = []
    for month, db_id in db_map.items():
        # 현재 월이거나 강제 갱신이면 API에서 fetch
        if month == current_month or force_refresh:
            logger.info("API fetch: get-ASAP %s", month)
            pages = _fetch_all_pages(db_id)
            df = _parse_pages(pages)
            _save_cache(df, month)
        else:
            df = _load_cache(month)
            if df is None:
                logger.info("캐시 없음, API fetch: get-ASAP %s", month)
                pages = _fetch_all_pages(db_id)
                df = _parse_pages(pages)
                _save_cache(df, month)
        frames.append(df)

    return pd.concat(frames, ignore_index=True)
