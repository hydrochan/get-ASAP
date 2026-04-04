"""Notion DB CRUD 모듈 (NOTION-01, NOTION-02, NOTION-03)

notion-client SDK와 이름 충돌 방지를 위해 notion_client_mod.py로 명명.
(SDK 패키지명: notion_client, 본 모듈: notion_client_mod)

제공 기능:
- create_paper_db: Notion DB 신규 생성
- get_or_create_db: 환경변수 기반 DB 획득/생성
- save_paper: 단일 논문 저장 (중복 방지 포함)
- save_papers: 배치 저장 (중복 방지 + 진행률 출력)
"""
import logging
import time
from typing import Optional

from notion_client import APIResponseError

import config
from models import PaperMetadata
from notion_auth import get_notion_client


def create_paper_db(parent_page_id: str) -> str:
    """Notion 논문 DB 신규 생성 후 DB ID 반환 (per D-07, NOTION-01)

    부모 페이지 아래에 "get-ASAP Papers" DB를 7개 속성 스키마로 생성.
    """
    client = get_notion_client()
    response = client.databases.create(
        parent={"type": "page_id", "page_id": parent_page_id},
        title=[{"type": "text", "text": {"content": "get-ASAP Papers"}}],
        initial_data_source={
            "properties": {
                "Title": {"type": "title", "title": {}},
                "DOI": {"type": "rich_text", "rich_text": {}},
                "Journal": {"type": "select", "select": {}},
                "Date": {"type": "date", "date": {}},
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
                "URL": {"type": "url", "url": {}},
                "Authors": {"type": "rich_text", "rich_text": {}},
            }
        },
    )
    return response["id"]


def _get_data_source_id(database_id: str) -> str:
    """DB의 첫 번째 data_source_id 반환 (notion-client 3.0.0 필수 discovery 단계)

    databases.retrieve로 DB 정보를 가져온 후 data_sources[0]["id"]를 반환.
    data_sources 배열이 비어있으면 ValueError 발생.
    """
    client = get_notion_client()
    db_info = client.databases.retrieve(database_id)
    data_sources = db_info.get("data_sources", [])
    if not data_sources:
        raise ValueError(
            f"데이터베이스 '{database_id}'에 data_sources가 없습니다. "
            "DB가 올바르게 생성되었는지 확인하세요."
        )
    return data_sources[0]["id"]


def get_or_create_db() -> str:
    """환경변수 기반 DB ID 반환 (per D-08)

    NOTION_DATABASE_ID가 있으면 기존 DB 사용.
    없으면 NOTION_PARENT_PAGE_ID로 새 DB 생성.
    둘 다 없으면 ValueError 발생.
    """
    if config.NOTION_DATABASE_ID:
        return config.NOTION_DATABASE_ID

    # 신규 DB 생성 경로
    if not config.NOTION_PARENT_PAGE_ID:
        raise ValueError(
            "NOTION_DATABASE_ID 또는 NOTION_PARENT_PAGE_ID 환경변수가 필요합니다. "
            ".env 파일에 둘 중 하나를 설정하세요."
        )
    return create_paper_db(config.NOTION_PARENT_PAGE_ID)


def _build_properties(paper: PaperMetadata) -> dict:
    """PaperMetadata → Notion pages.create용 properties 딕셔너리 변환 (per D-01)

    Status는 항상 "대기중"으로 설정 (per D-02).
    선택 필드(URL, Authors)는 값이 있을 때만 포함.
    """
    props = {
        "Title": {
            "title": [{"type": "text", "text": {"content": paper.title}}]
        },
        "DOI": {
            "rich_text": [{"type": "text", "text": {"content": paper.doi or ""}}]
        },
        "Journal": {
            "select": {"name": paper.journal}
        },
        "Status": {
            "select": {"name": "대기중"}  # 항상 대기중으로 저장 (per D-02)
        },
    }

    # Date: 값이 있을 때만 포함
    if paper.date:
        props["Date"] = {"date": {"start": paper.date}}

    # URL: 선택 필드 (per D-01)
    if paper.url:
        props["URL"] = {"url": paper.url}

    # Authors: 선택 필드, 콤마+공백으로 결합 (per D-01)
    if paper.authors:
        props["Authors"] = {
            "rich_text": [
                {"type": "text", "text": {"content": ", ".join(paper.authors)}}
            ]
        }

    return props


def _is_duplicate(data_source_id: str, doi: str, title: str) -> bool:
    """DOI 또는 제목 기반 중복 여부 확인 (per D-03, D-04)

    DOI 있으면 DOI 정확 일치 검사 (D-03).
    DOI 없으면 제목 포함 검사 (D-04).
    """
    client = get_notion_client()

    if doi:
        # DOI 기반 정확 일치 검사 (D-03)
        result = client.data_sources.query(
            data_source_id,
            filter={"property": "DOI", "rich_text": {"equals": doi}},
            page_size=1,
        )
    else:
        # 제목 기반 포함 검사 (D-04) — Title은 title 타입 필터 사용 (rich_text 아님)
        result = client.data_sources.query(
            data_source_id,
            filter={"property": "Title", "title": {"contains": title}},
            page_size=1,
        )

    return len(result["results"]) > 0


def _call_with_retry(fn, *args, **kwargs):
    """rate_limited 에러 시 1초 대기 후 1회 재시도 (per D-12)

    rate_limited(429): 1초 sleep 후 재시도. 재시도도 실패하면 warning + None 반환.
    그 외 APIResponseError: warning 로그 + None 반환 (per D-11).
    """
    try:
        return fn(*args, **kwargs)
    except APIResponseError as e:
        if e.code == "rate_limited":
            # rate limit 에러: 1초 후 재시도 (per D-12)
            time.sleep(1)
            try:
                return fn(*args, **kwargs)
            except APIResponseError as retry_err:
                logging.warning(f"재시도 후 Notion API 실패: {retry_err}")
                return None
        else:
            # 그 외 API 에러: 스킵 (per D-11)
            logging.warning(f"Notion API 오류 (스킵): {e}")
            return None


def save_paper(paper: PaperMetadata, database_id: str, data_source_id: str) -> bool:
    """단일 논문 저장 (per D-09, NOTION-02)

    중복 검사 후 신규 논문만 pages.create로 저장.
    중복 시 logging.info + False 반환 (per D-05).
    저장 성공 시 True, API 실패 시 False 반환.
    """
    # 중복 검사 (per D-03, D-04)
    if _is_duplicate(data_source_id, paper.doi, paper.title):
        logging.info(f"중복 스킵: {paper.doi or paper.title}")
        return False

    # pages.create 호출 (rate limit 재시도 포함)
    client = get_notion_client()
    result = _call_with_retry(
        client.pages.create,
        parent={"database_id": database_id},
        properties=_build_properties(paper),
    )

    # result가 None이면 API 실패 (warning은 _call_with_retry에서 이미 출력)
    return result is not None


def save_papers(papers: list[PaperMetadata], database_id: str) -> dict:
    """배치 논문 저장 (per D-10, NOTION-02)

    data_source_id는 1회만 획득 후 재사용 (캐싱).
    진행률을 logging.info로 출력.
    반환: {"saved": N, "skipped": M, "failed": K}
    """
    # data_source_id 획득 (1회만, 캐싱)
    data_source_id = _get_data_source_id(database_id)

    saved = 0
    skipped = 0
    failed = 0
    total = len(papers)

    for i, paper in enumerate(papers):
        logging.info(f"저장 중: {i + 1}/{total} - {paper.title[:50]}")

        # save_paper 내부에서 중복 검사 + 저장 수행
        # True: 저장 성공, False: 중복 스킵 또는 API 실패
        # 중복 스킵 판별: _is_duplicate에서 logging.info 출력 후 False 반환
        result = _save_paper_with_status(paper, database_id, data_source_id)

        if result == "saved":
            saved += 1
        elif result == "skipped":
            skipped += 1
        else:
            failed += 1

    return {"saved": saved, "skipped": skipped, "failed": failed}


def _save_paper_with_status(
    paper: PaperMetadata, database_id: str, data_source_id: str
) -> str:
    """save_paper의 결과를 "saved"/"skipped"/"failed" 문자열로 반환 (내부 사용)

    save_papers의 집계를 위해 중복 스킵과 API 실패를 구분하는 내부 헬퍼.
    """
    # 중복 검사 (per D-03, D-04)
    if _is_duplicate(data_source_id, paper.doi, paper.title):
        logging.info(f"중복 스킵: {paper.doi or paper.title}")
        return "skipped"

    # pages.create 호출 (rate limit 재시도 포함)
    client = get_notion_client()
    result = _call_with_retry(
        client.pages.create,
        parent={"database_id": database_id},
        properties=_build_properties(paper),
    )

    return "saved" if result is not None else "failed"
