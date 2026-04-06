"""Notion DB CRUD 모듈 (NOTION-01, NOTION-02, NOTION-03)

notion-client SDK와 이름 충돌 방지를 위해 notion_client_mod.py로 명명.

제공 기능:
- create_paper_db: Notion DB 신규 생성
- get_or_create_db: 환경변수 기반 DB 획득/생성
- save_paper: 단일 논문 저장 (중복 방지 포함)
- save_papers: 배치 저장 (중복 방지 + 진행률 출력)
"""
import logging
import time

from notion_client import APIResponseError

import config
from models import PaperMetadata
from notion_auth import get_notion_client


def create_paper_db(parent_page_id: str) -> str:
    """Notion 논문 DB 신규 생성 후 DB ID 반환 (NOTION-01)

    속성: Title, Journal, Date, Status
    """
    client = get_notion_client()
    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "get-ASAP Papers"}}],
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


def _get_data_source_id(database_id: str) -> str:
    """DB의 첫 번째 data_source_id 반환 (notion-client 3.0.0 필수)"""
    client = get_notion_client()
    db_info = client.databases.retrieve(database_id)
    data_sources = db_info.get("data_sources", [])
    if not data_sources:
        raise ValueError(
            f"데이터베이스 '{database_id}'에 data_sources가 없습니다."
        )
    return data_sources[0]["id"]


def get_or_create_db() -> str:
    """환경변수 기반 DB ID 반환"""
    if config.NOTION_DATABASE_ID:
        return config.NOTION_DATABASE_ID

    if not config.NOTION_PARENT_PAGE_ID:
        raise ValueError(
            "NOTION_DATABASE_ID 또는 NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. "
            ".env 파일에 둘 중 하나를 설정하세요."
        )
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


def _is_duplicate(data_source_id: str, title: str) -> bool:
    """제목 기반 중복 여부 확인 (NOTION-03)"""
    client = get_notion_client()
    result = client.data_sources.query(
        data_source_id,
        filter={"property": "Title", "title": {"equals": title}},
        page_size=1,
    )
    return len(result["results"]) > 0


def _call_with_retry(fn, *args, **kwargs):
    """rate_limited 에러 시 1초 대기 후 1회 재시도"""
    try:
        return fn(*args, **kwargs)
    except APIResponseError as e:
        if e.code == "rate_limited":
            time.sleep(1)
            try:
                return fn(*args, **kwargs)
            except APIResponseError as retry_err:
                logging.warning("재시도 후 Notion API 실패: %s", retry_err)
                return None
        else:
            logging.warning("Notion API 오류 (스킵): %s", e)
            return None


def save_paper(paper: PaperMetadata, database_id: str, data_source_id: str) -> bool:
    """단일 논문 저장 (NOTION-02). 중복 시 스킵."""
    if _is_duplicate(data_source_id, paper.title):
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
    data_source_id = _get_data_source_id(database_id)

    saved = 0
    skipped = 0
    failed = 0
    total = len(papers)

    for i, paper in enumerate(papers):
        logging.info("저장 중: %d/%d - %s", i + 1, total, paper.title[:50])

        if _is_duplicate(data_source_id, paper.title):
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
