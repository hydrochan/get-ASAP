"""Notion DB CRUD 모듈 (NOTION-01, NOTION-02, NOTION-03)

notion-client SDK와 이름 충돌 방지를 위해 notion_client_mod.py로 명명.

제공 기능:
- create_paper_db: Notion DB 신규 생성
- get_or_create_db: 월별 DB 자동 획득/생성
- save_paper: 단일 논문 저장 (중복 방지 포함)
- save_papers: 배치 저장 (중복 방지 + 진행률 출력)
"""
import logging
import time
from datetime import date

from notion_client import APIResponseError

import config
from models import PaperMetadata
from notion_auth import get_notion_client


def create_paper_db(parent_page_id: str, db_name: str = None) -> str:
    """Notion 논문 DB 신규 생성 후 DB ID 반환 (NOTION-01)

    속성: Title, Journal, Date, URL, Status
    """
    if db_name is None:
        db_name = f"get-ASAP {date.today().strftime('%Y-%m')}"
    client = get_notion_client()
    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": db_name}}],
        initial_data_source={
            "properties": {
                "Title": {"type": "title", "title": {}},
                "Journal": {"type": "select", "select": {}},
                "Date": {"type": "date", "date": {}},
                "URL": {"type": "url", "url": {}},
                "Status": {
                    "type": "select",
                    "select": {
                        "options": [
                            {"name": "대기중", "color": "yellow"},
                            {"name": "읽음", "color": "green"},
                            {"name": "관심", "color": "blue"},
                            {"name": "스킵", "color": "gray"},
                        ]
                    },
                },
            }
        },
    )
    return response["id"]




def _find_monthly_db(parent_page_id: str, month_str: str) -> str | None:
    """parent page 하위에서 'get-ASAP YYYY-MM' 이름의 DB를 찾아 ID 반환. 없으면 None."""
    client = get_notion_client()
    db_name = f"get-ASAP {month_str}"
    # parent page의 자식 블록에서 child_database 타입 검색
    cursor = None
    while True:
        kwargs = {"block_id": parent_page_id, "page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.blocks.children.list(**kwargs)
        for block in result["results"]:
            if block["type"] != "child_database":
                continue
            title_parts = block.get("child_database", {}).get("title", "")
            if title_parts == db_name:
                return block["id"]
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return None


def get_or_create_db() -> str:
    """월별 DB 자동 획득/생성. 'get-ASAP YYYY-MM' 형식."""
    if not config.NOTION_PARENT_PAGE_ID:
        if config.NOTION_DATABASE_ID:
            return config.NOTION_DATABASE_ID
        raise ValueError(
            "NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. "
            ".env 파일에 설정하세요."
        )

    month_str = date.today().strftime("%Y-%m")
    # 기존 월별 DB 검색
    db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, month_str)
    if db_id:
        logging.info("기존 월별 DB 사용: get-ASAP %s", month_str)
        return db_id

    # 없으면 새로 생성
    logging.info("월별 DB 생성: get-ASAP %s", month_str)
    return create_paper_db(config.NOTION_PARENT_PAGE_ID)


def _build_properties(paper: PaperMetadata) -> dict:
    """PaperMetadata -> Notion properties 변환"""
    props = {
        "Title": {
            "title": [{"type": "text", "text": {"content": paper.title}}]
        },
        "Status": {
            "select": {"name": "대기중"}
        },
    }

    if paper.journal:
        props["Journal"] = {"select": {"name": paper.journal}}

    if paper.date:
        props["Date"] = {"date": {"start": paper.date}}

    if paper.url:
        props["URL"] = {"url": paper.url}

    return props


def _is_duplicate(database_id: str, title: str) -> bool:
    """제목 기반 중복 여부 확인 (NOTION-03)

    Notion REST API POST /databases/{id}/query 직접 호출.
    notion-client 3.0.0은 databases.query 메서드가 없고,
    data_sources.query의 title equals 필터는 작동하지 않으므로 httpx 직접 사용.
    """
    import httpx
    resp = httpx.post(
        f"https://api.notion.com/v1/databases/{database_id}/query",
        headers={
            "Authorization": f"Bearer {config.NOTION_TOKEN}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
        json={
            "filter": {"property": "Title", "title": {"equals": title}},
            "page_size": 1,
        },
        timeout=30,
    )
    resp.raise_for_status()
    return len(resp.json()["results"]) > 0


def _call_with_retry(fn, *args, max_retries=3, **kwargs):
    """rate_limited 에러 시 백오프 재시도 (최대 3회)"""
    for attempt in range(max_retries + 1):
        try:
            return fn(*args, **kwargs)
        except APIResponseError as e:
            if e.code == "rate_limited" and attempt < max_retries:
                wait = 2 ** attempt  # 1, 2, 4초
                logging.info("Rate limited, %d초 후 재시도 (%d/%d)", wait, attempt + 1, max_retries)
                time.sleep(wait)
                continue
            elif e.code == "rate_limited":
                logging.warning("Rate limit 재시도 초과: %s", e)
                return None
            else:
                logging.warning("Notion API 오류 (스킵): %s", e)
                return None


def save_paper(paper: PaperMetadata, database_id: str) -> bool:
    """단일 논문 저장 (NOTION-02). 중복 시 스킵."""
    if _is_duplicate(database_id, paper.title):
        logging.info("중복 스킵: %s", paper.title[:60])
        return False

    client = get_notion_client()
    result = _call_with_retry(
        client.pages.create,
        parent={"database_id": database_id},
        properties=_build_properties(paper),
    )
    return result is not None


def save_papers(papers: list[PaperMetadata], database_id: str) -> dict:
    """배치 논문 저장. 반환: {"saved": N, "skipped": M, "failed": K}"""
    saved = 0
    skipped = 0
    failed = 0
    total = len(papers)

    for i, paper in enumerate(papers):
        logging.info("저장 중: %d/%d - %s", i + 1, total, paper.title[:50])

        if _is_duplicate(database_id, paper.title):
            logging.info("중복 스킵: %s", paper.title[:60])
            skipped += 1
            continue

        client = get_notion_client()
        result = _call_with_retry(
            client.pages.create,
            parent={"database_id": database_id},
            properties=_build_properties(paper),
        )

        if result is not None:
            saved += 1
        else:
            failed += 1

    return {"saved": saved, "skipped": skipped, "failed": failed}
