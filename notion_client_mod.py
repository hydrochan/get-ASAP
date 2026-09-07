"""Notion DB CRUD 모듈 (NOTION-01, NOTION-02, NOTION-03)

notion-client SDK와 이름 충돌 방지를 위해 notion_client_mod.py로 명명.

제공 기능:
- create_paper_db: Notion DB 신규 생성
- get_or_create_db: 이번 달 월별 DB 자동 획득/생성
- resolve_db_for_month: 논문 발행월 기준 저장 대상 DB 결정 (과거월 DB는 자동 생성하지 않음, 2026-09-07)
- save_paper: 단일 논문 저장 (중복 방지 포함)
- save_papers: 배치 저장 (중복 방지 + 진행률 출력)
"""
import logging
import re
import time
from datetime import date
from datetime import datetime
import unicodedata

from notion_client import APIResponseError

import config
from models import PaperMetadata
from notion_auth import get_notion_client


def create_paper_db(parent_page_id: str, db_name: str = None) -> str:
    """Notion 논문 DB 신규 생성 후 DB ID 반환 (NOTION-01)

    속성: Title, Journal, Date, URL, Status, GPT Reason
    - Status: get-ASAP 은 '대기중' 만 쓰지만, 하류(paper_autodown)가 쓰는
      관련/불확실/다운완료/무관 까지 명시해 월별 DB UI 가 일관되게 유지.

    주의: 과거(2026-04-19~21) 'Zotero Key' (rich_text) 컬럼을 추가했더니,
    DB 규모가 수천 건 누적된 시점에 Notion `data_sources.query` API 가
    pagination 을 300건에서 조기 종료하는 버그가 발생. 컬럼 DROP 즉시 회복됨.
    재현 조건이 불명확해 해당 컬럼은 도입하지 않는다. Notion↔Zotero 매핑은
    paper_autodown 의 state.db (notion_page_id ↔ zotero_key) 로만 관리한다.
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
                            {"name": "관련", "color": "green"},
                            {"name": "불확실", "color": "yellow"},
                            {"name": "다운완료", "color": "purple"},
                            {"name": "무관", "color": "red"},
                        ]
                    },
                },
                "GPT Reason": {"type": "rich_text", "rich_text": {}},
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
    """이번 달 월별 DB 자동 획득/생성. 'get-ASAP YYYY-MM' 형식 (오늘 날짜 기준 월 고정).

    항상 오늘 날짜 기준 월의 DB만 반환한다. 논문 자체의 발행월로 저장 DB를
    정하려면 resolve_db_for_month()를 쓸 것 (2026-09-07 도입 - ACS ToC 메일이
    며칠 늦게 도착해 발행월과 수신월이 어긋나는 문제 대응).
    """
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


_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def resolve_db_for_month(month_str: str, current_db_id: str, cache: dict) -> str:
    """논문 발행월(month_str, 'YYYY-MM')에 맞는 저장 대상 DB id를 반환 (2026-09-07 도입).

    배경: ACS ToC 알림 메일이 웹 발행 며칠 뒤에 도착해, get_or_create_db()가
    반환하는 "오늘 날짜 기준" DB에 지난달 논문이 잘못 쌓이는 문제가 있었다
    (backfill_acs.py가 이미 우회 중이던 문제, 2026-09-01/07에 8월 논문 387건이
    9월 DB로 들어감). 이 함수는 Gmail 정규 경로에도 같은 월별 라우팅을 적용한다.

    정책:
    - month_str이 이번 달이면 current_db_id를 그대로 반환한다.
    - month_str이 과거 달이고 해당 월 DB가 이미 있으면 그 DB로 라우팅한다.
      과거 달 DB를 새로 만들지는 않는다(과거월 DB 생성은 backfill 스크립트
      전용 - Gmail 경로가 만들면 그 사이 도착한 다른 메일과 경합할 수 있다).
    - 그 외(과거 달인데 DB가 없음 / 미래 달 / 날짜 형식이 아님)는
      current_db_id로 폴백하고 경고 로그를 남긴다.

    Args:
        month_str: 논문 발행월 "YYYY-MM" (형식이 아니면 폴백)
        current_db_id: get_or_create_db()가 반환한 이번 달 DB id
        cache: 실행(run) 동안 재사용하는 dict. 같은 월에 대해
            _find_monthly_db() 호출을 실행당 최대 1회로 제한한다(호출부가
            매 실행마다 새 dict를 넘겨야 함).

    Returns:
        저장에 사용할 Notion database_id
    """
    today_month = date.today().strftime("%Y-%m")

    if not month_str or not _MONTH_RE.match(month_str):
        logging.warning(
            "월 라우팅: 논문 발행월(%r)을 해석할 수 없어 현재월 DB로 폴백", month_str
        )
        return current_db_id

    if month_str == today_month:
        return current_db_id

    if month_str > today_month:
        logging.warning(
            "월 라우팅: 논문 발행월(%s)이 미래 달이라 현재월 DB로 폴백", month_str
        )
        return current_db_id

    if month_str in cache:
        return cache[month_str]

    if not config.NOTION_PARENT_PAGE_ID:
        logging.warning(
            "월 라우팅: NOTION_PARENT_PAGE_ID 미설정으로 과거월(%s) DB를 찾을 수 없어 현재월 DB로 폴백",
            month_str,
        )
        cache[month_str] = current_db_id
        return current_db_id

    try:
        db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, month_str)
    except Exception as e:
        # 조회 자체가 실패한 경우(네트워크/API 오류)도 "DB 없음"과 동일하게
        # 취급한다. 여기서 예외를 던지면 같은 메일 안의 다른(정상) 월 그룹까지
        # 통째로 재시도 대기로 밀려나므로, 과거월 조회는 best-effort로 다룬다.
        logging.warning("월 라우팅: 과거월(%s) DB 조회 실패, 현재월 DB로 폴백: %s", month_str, e)
        cache[month_str] = current_db_id
        return current_db_id

    if db_id:
        cache[month_str] = db_id
        return db_id

    logging.warning(
        "월 라우팅: 과거월(%s) DB가 없어 현재월 DB로 폴백 (과거월 DB는 Gmail 경로에서 자동 생성하지 않음)",
        month_str,
    )
    cache[month_str] = current_db_id
    return current_db_id


def _previous_month_str(month_str: str) -> str:
    """'YYYY-MM'의 직전 월을 반환."""
    current = datetime.strptime(month_str, "%Y-%m")
    if current.month == 1:
        return f"{current.year - 1}-12"
    return f"{current.year}-{current.month - 1:02d}"


def _previous_months(month_str: str, count: int = 2) -> list[str]:
    """'YYYY-MM' 기준 직전 N개월 목록을 최근 월부터 반환."""
    months = []
    current = month_str
    for _ in range(count):
        current = _previous_month_str(current)
        months.append(current)
    return months


def _duplicate_check_database_ids(database_id: str) -> list[str]:
    """중복 확인 대상 DB 목록.

    대상(database_id) + 오늘 날짜 기준 이번달 DB(대상과 다를 때만) + 직전
    2개월 DB를 함께 본다. 이번달 DB를 항상 포함시키는 이유(2026-09-07): 논문
    발행월 DB로 저장하는 경로(resolve_db_for_month)가 생기면서, 이미 이번달
    DB에 들어간 논문(예: 수신이 늦어 이번달에 먼저 저장된 지난달 논문)이
    과거월 DB로 다시 중복 저장되는 것을 막아야 한다.
    """
    db_ids = [database_id]
    if not config.NOTION_PARENT_PAGE_ID:
        return db_ids

    today_month = date.today().strftime("%Y-%m")
    try:
        current_db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, today_month)
    except Exception as e:
        logging.warning("이번달 DB 중복 체크 준비 실패 (%s): %s", today_month, e)
        current_db_id = None
    if current_db_id and current_db_id not in db_ids:
        db_ids.append(current_db_id)

    for month in _previous_months(today_month, count=2):
        try:
            prev_db_id = _find_monthly_db(config.NOTION_PARENT_PAGE_ID, month)
        except Exception as e:
            logging.warning("과거 월 DB 중복 체크 준비 실패 (%s): %s", month, e)
            continue
        if prev_db_id and prev_db_id not in db_ids:
            db_ids.append(prev_db_id)
    return db_ids


def _normalize_title_for_duplicate(title: str) -> str:
    """메일/출판사별 사소한 표기 차이를 줄인 배치 중복 키."""
    normalized = unicodedata.normalize("NFKC", title or "")
    normalized = normalized.replace("‐", "-").replace("‑", "-").replace("‒", "-")
    normalized = normalized.replace("–", "-").replace("—", "-").replace("−", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().casefold()


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
    간헐적 404/429 에러 시 백오프 재시도.
    """
    import httpx
    url = f"https://api.notion.com/v1/databases/{database_id}/query"
    headers = {
        "Authorization": f"Bearer {config.NOTION_TOKEN}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json",
    }
    payload = {
        "filter": {"property": "Title", "title": {"equals": title}},
        "page_size": 1,
    }
    # 네트워크 예외(타임아웃/연결 오류)도 재시도 대상 — 파이프라인 전체 크래시 방지
    # 재시도: 1→2→4→8→16초 (최대 5회, 총 대기 약 31초)
    for attempt in range(5):
        try:
            resp = httpx.post(url, headers=headers, json=payload, timeout=30)
        except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
            wait = 2 ** attempt
            logging.warning("중복 체크 네트워크 에러(%s), %d초 후 재시도 (%d/5)", type(e).__name__, wait, attempt + 1)
            time.sleep(wait)
            continue
        if resp.status_code == 200:
            return len(resp.json()["results"]) > 0
        if resp.status_code in (404, 429, 500, 502, 503, 504):
            wait = 2 ** attempt
            logging.warning("중복 체크 %d 에러, %d초 후 재시도 (%d/5)", resp.status_code, wait, attempt + 1)
            time.sleep(wait)
            continue
        resp.raise_for_status()
    # 재시도 초과 시 안전하게 중복 아님으로 처리 (저장 시도 — 실제 중복이면 Notion이 거부 안 하고 신규 생성함.
    # 진짜 중복이 생길 수 있지만 파이프라인이 크래시하는 것보다는 낫다.)
    logging.warning("중복 체크 재시도 초과, 저장 시도로 진행 (중복일 경우 수동 정리 필요)")
    return False


def _is_duplicate_in_databases(database_ids: list[str], title: str) -> bool:
    """여러 월별 DB 중 하나라도 같은 제목이 있으면 중복으로 본다."""
    for db_id in database_ids:
        if _is_duplicate(db_id, title):
            return True
    return False


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
    duplicate_db_ids = _duplicate_check_database_ids(database_id)
    if _is_duplicate_in_databases(duplicate_db_ids, paper.title):
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
    duplicate_db_ids = _duplicate_check_database_ids(database_id)
    seen_titles: set[str] = set()

    for i, paper in enumerate(papers):
        logging.info("저장 중: %d/%d - %s", i + 1, total, paper.title[:50])

        title_key = _normalize_title_for_duplicate(paper.title)
        if title_key in seen_titles:
            logging.info("배치 내 중복 스킵: %s", paper.title[:60])
            skipped += 1
            continue

        if _is_duplicate_in_databases(duplicate_db_ids, paper.title):
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
            seen_titles.add(title_key)
        else:
            failed += 1

    return {"saved": saved, "skipped": skipped, "failed": failed}
