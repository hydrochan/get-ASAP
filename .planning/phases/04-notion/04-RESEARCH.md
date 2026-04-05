# Phase 4: Notion 통합 및 중복 방지 - Research

**Researched:** 2026-04-05
**Domain:** Notion API (notion-client 3.0.0 / API version 2025-09-03)
**Confidence:** HIGH

## Summary

notion-client 3.0.0이 설치되어 있으며 Notion API 버전 `2025-09-03`을 사용한다. 이 버전은 2025년 9월에 도입된 "Data Sources" 모델로 전환된 중요한 변경점이 있다. 구버전 `databases.query()` 메서드가 SDK에서 완전히 제거되었고, `data_sources.query(data_source_id)` 메서드로 대체되었다. 기존 database_id에서 data_source_id를 추출하는 discovery 단계가 필요하다.

DB 생성 시 속성 스키마는 `initial_data_source.properties` 아래에 위치하며, 페이지 생성(pages.create)과 에러 처리(APIResponseError) 패턴은 이전 phases에서 확립된 것과 동일하다. rate limit 에러 코드는 `APIErrorCode.RateLimited = "rate_limited"`이고, status 코드는 429이다.

**Primary recommendation:** `data_sources.query(data_source_id)` 사용 전 `databases.retrieve(db_id)` 로 data_source_id를 먼저 획득한다. pages.create는 변경 없이 database_id를 parent로 사용한다.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** PaperMetadata 필드를 Notion DB 속성으로 매핑:
  - title → Title 속성 (Notion 기본 제목)
  - doi → Rich Text 속성 (중복 검색 필터에 사용)
  - journal → Select 속성 (저널명 자동 옵션화)
  - date → Date 속성 (ISO 형식)
  - 상태 → Select 속성 (기본값: "대기중")
  - url → URL 속성 (선택, 있으면 저장)
  - authors → Rich Text 속성 (선택, 콤마 구분)
- **D-02:** DB 제목은 "get-ASAP Papers" 또는 사용자 지정 가능
- **D-03:** 저장 전 `databases.query(filter={doi=X})`로 기존 논문 확인 — DOI 일치하면 스킵 + 로그
  *(주의: 실제 API는 data_sources.query로 대체됨 — 아래 Standard Stack 참고)*
- **D-04:** DOI가 비어있는 논문은 제목 기반 중복 검사 (title 필드 contains 필터)
- **D-05:** 중복 발견 시 logging.info로 기록 후 건너뜀 (덮어쓰기 없음)
- **D-06:** notion_client.py 모듈에 Notion DB CRUD 기능 통합
- **D-07:** `create_paper_db(parent_page_id)` — 최초 1회 DB 생성 함수
- **D-08:** `NOTION_DATABASE_ID` 환경변수가 있으면 기존 DB 사용, 없으면 신규 생성
- **D-09:** `save_paper(paper: PaperMetadata)` — 단일 논문 저장 함수
- **D-10:** `save_papers(papers: list[PaperMetadata])` — 배치 저장 + 중복 검사 통합
- **D-11:** Notion API 실패 시 logging.warning + 스킵 (Phase 3 파서와 동일 패턴)
- **D-12:** API rate limit(429) 시 1회 sleep(1초) 후 재시도, 재실패 시 스킵

### Claude's Discretion
- Notion API 페이지네이션 처리 방식
- DB 생성 시 parent page 선택 로직
- 배치 저장 시 진행률 출력 여부
- .env.example에 NOTION_DATABASE_ID 추가 여부

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| NOTION-01 | Notion에 논문 DB를 새로 생성할 수 있다 (제목, DOI, 저널명, 날짜, 상태 속성) | databases.create + initial_data_source.properties 스키마 패턴 확인 |
| NOTION-02 | 추출된 논문 데이터를 Notion DB에 페이지로 저장할 수 있다 (상태="대기중") | pages.create properties 구조 확인 |
| NOTION-03 | DOI 기반으로 중복 논문 저장을 방지할 수 있다 | data_sources.query + rich_text.equals 필터 패턴 확인 |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| notion-client | 3.0.0 (설치됨) | Notion API 클라이언트 | 이미 설치, Notion API 2025-09-03 지원 |
| notion_auth.get_notion_client() | (Phase 1) | 인증된 Client 반환 | Phase 1에서 구현 완료, 재사용 |

### Critical API Mapping (notion-client 3.0.0)

| 작업 | SDK 메서드 | 비고 |
|------|-----------|------|
| DB 생성 | `client.databases.create(parent=..., title=..., initial_data_source=...)` | 스키마는 initial_data_source 아래 |
| DB data_source_id 획득 | `client.databases.retrieve(database_id)["data_sources"][0]["id"]` | query 전 필수 discovery 단계 |
| DB 쿼리 (중복 검사) | `client.data_sources.query(data_source_id, filter=..., page_size=1)` | databases.query 없음 |
| 페이지 생성 | `client.pages.create(parent={"database_id": db_id}, properties=...)` | 변경 없음 |

**중요:** notion-client 3.0.0에서 `client.databases.query()`가 존재하지 않는다. `DatabasesEndpoint`에는 `retrieve`, `update`, `create`만 있다. 중복 검사는 `client.data_sources.query(data_source_id)`를 사용해야 한다.

---

## Architecture Patterns

### Recommended Project Structure
```
notion_client.py      # 새로 생성 (루트, 플랫 구조 유지)
tests/
└── test_notion_client.py   # TDD 테스트 파일
```

### Pattern 1: DB 생성 (databases.create)
**What:** parent page 아래에 "get-ASAP Papers" DB를 논문 스키마로 생성
**When to use:** NOTION_DATABASE_ID 환경변수가 없을 때 (D-08)

```python
# Source: Notion API docs (2025-09-03) + api_endpoints.py 직접 확인
def create_paper_db(parent_page_id: str) -> str:
    """Notion 논문 DB 생성. DB ID 반환"""
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
                "Status": {"type": "select", "select": {
                    "options": [
                        {"name": "대기중", "color": "yellow"},
                        {"name": "읽음", "color": "green"},
                        {"name": "관심", "color": "blue"},
                        {"name": "스킵", "color": "gray"},
                    ]
                }},
                "URL": {"type": "url", "url": {}},
                "Authors": {"type": "rich_text", "rich_text": {}},
            }
        }
    )
    return response["id"]
```

### Pattern 2: data_source_id Discovery
**What:** database_id로부터 data_source_id를 추출 (중복 검사에 필요)
**When to use:** data_sources.query 호출 전 1회 수행

```python
# Source: Notion API upgrade guide 2025-09-03
def _get_data_source_id(database_id: str) -> str:
    """DB의 첫 번째 data_source_id 반환"""
    client = get_notion_client()
    db_info = client.databases.retrieve(database_id)
    return db_info["data_sources"][0]["id"]
```

### Pattern 3: 중복 검사 (data_sources.query)
**What:** DOI 또는 제목으로 기존 페이지 존재 여부 확인
**When to use:** save_paper 호출 시 매번

```python
# Source: Notion API docs (2025-09-03) + data_sources query endpoint
def _is_duplicate(data_source_id: str, doi: str, title: str) -> bool:
    """DOI 또는 제목 기반 중복 여부 확인"""
    client = get_notion_client()

    if doi:
        # DOI 기반 정확 일치 검사 (D-03)
        result = client.data_sources.query(
            data_source_id,
            filter={"property": "DOI", "rich_text": {"equals": doi}},
            page_size=1,
        )
    else:
        # 제목 기반 검사 (D-04)
        result = client.data_sources.query(
            data_source_id,
            filter={"property": "Title", "title": {"contains": title}},
            page_size=1,
        )
    return len(result["results"]) > 0
```

### Pattern 4: 페이지 생성 (pages.create)
**What:** PaperMetadata → Notion DB 페이지 저장

```python
# Source: Notion API docs (2025-09-03)
def _build_properties(paper: PaperMetadata) -> dict:
    """PaperMetadata → Notion properties 딕셔너리 변환"""
    props = {
        "Title": {"title": [{"type": "text", "text": {"content": paper.title}}]},
        "DOI": {"rich_text": [{"type": "text", "text": {"content": paper.doi or ""}}]},
        "Journal": {"select": {"name": paper.journal}},
        "Status": {"select": {"name": "대기중"}},
    }
    if paper.date:
        props["Date"] = {"date": {"start": paper.date}}
    if paper.url:
        props["URL"] = {"url": paper.url}
    if paper.authors:
        props["Authors"] = {
            "rich_text": [{"type": "text", "text": {"content": ", ".join(paper.authors)}}]
        }
    return props

def save_paper(paper: PaperMetadata, database_id: str, data_source_id: str) -> bool:
    """단일 논문 저장. 중복 시 False, 저장 성공 시 True 반환"""
    if _is_duplicate(data_source_id, paper.doi, paper.title):
        logging.info(f"중복 스킵: {paper.doi or paper.title}")
        return False
    client = get_notion_client()
    client.pages.create(
        parent={"database_id": database_id},
        properties=_build_properties(paper),
    )
    return True
```

### Pattern 5: rate limit 재시도 (D-12)
```python
import time
from notion_client import APIResponseError

def _call_with_retry(fn, *args, **kwargs):
    """rate_limited 에러 시 1초 대기 후 1회 재시도"""
    try:
        return fn(*args, **kwargs)
    except APIResponseError as e:
        if e.code == "rate_limited":  # APIErrorCode.RateLimited.value
            time.sleep(1)
            try:
                return fn(*args, **kwargs)
            except APIResponseError:
                logging.warning(f"재시도 후 Notion API 실패: {e}")
                return None
        raise
```

### Anti-Patterns to Avoid
- **`client.databases.query()` 호출:** notion-client 3.0.0에서 이 메서드가 존재하지 않는다. `data_sources.query()` 사용.
- **data_source_id 없이 query:** `data_sources.query`는 data_source_id가 필수다. DB 저장 전 discovery 단계를 건너뛰면 AttributeError 발생.
- **`initial_data_source` 생략한 DB 생성:** 속성 스키마를 `properties`에 직접 넣으면 ValidationError. `initial_data_source.properties` 아래에 넣어야 한다.
- **DOI 빈 문자열로 `equals` 필터:** 빈 DOI를 equals로 검색하면 DOI 없는 모든 논문이 매칭된다. D-04처럼 DOI 없으면 제목 기반으로 분기.
- **Title 필터에 `rich_text` 타입 사용:** Title 속성은 `title` 타입이므로 필터도 `"title": {"contains": ...}` 형식 사용.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| DB 쿼리 중복 검사 | 수동 페이지 순회 + 비교 | `data_sources.query(filter=...)` | 서버 사이드 필터로 O(1) 조회 |
| 에러 분류 | e.status == 429 직접 비교 | `e.code == "rate_limited"` | notion-client는 code 문자열로 에러 구분 |
| 페이지네이션 | while loop + next_cursor 수동 | `page_size=1` 로 첫 결과만 | 중복 검사는 존재 여부만 확인하면 충분 |

---

## Common Pitfalls

### Pitfall 1: databases.query 미존재 (notion-client 3.0.0)
**What goes wrong:** `client.databases.query(database_id, filter=...)` 호출 시 AttributeError
**Why it happens:** notion-client 3.0.0은 Notion API 2025-09-03을 사용하며 databases.query가 data_sources.query로 대체됨
**How to avoid:** `databases.retrieve(db_id)["data_sources"][0]["id"]`로 data_source_id 먼저 획득 후 `data_sources.query(data_source_id)` 사용
**Warning signs:** `AttributeError: 'DatabasesEndpoint' object has no attribute 'query'`

### Pitfall 2: DB 생성 시 properties 위치 오류
**What goes wrong:** `databases.create(properties={...})` 호출 시 ValidationError 또는 속성 미생성
**Why it happens:** Notion API 2025-09-03에서 스키마는 `initial_data_source.properties` 아래에 있어야 함
**How to avoid:** `databases.create(initial_data_source={"properties": {...}}, ...)`
**Warning signs:** 생성된 DB에 속성 컬럼이 없거나 400 ValidationError

### Pitfall 3: APIResponseError 생성자 시그니처
**What goes wrong:** 테스트에서 APIResponseError 직접 생성 시 TypeError
**Why it happens:** 시그니처: `(code, status, message, headers, raw_body_text, additional_data=None, request_id=None)` - headers는 `httpx.Headers` 객체
**How to avoid:** Phase 1 테스트 패턴 재사용: `APIResponseError("rate_limited", 429, "msg", MagicMock(), "")`
**Warning signs:** `TypeError: __init__() missing required positional argument`

### Pitfall 4: data_source_id 캐싱 누락
**What goes wrong:** save_papers 배치 처리 시 매 논문마다 databases.retrieve 호출 → API 호출 낭비
**Why it happens:** discovery 단계를 반복 호출
**How to avoid:** save_papers 함수 진입 시 1회만 data_source_id를 획득하고 내부에서 재사용
**Warning signs:** API 호출 횟수가 저장 논문 수의 2배 이상

### Pitfall 5: Title 속성 필터 타입
**What goes wrong:** `filter={"property": "Title", "rich_text": {"contains": ...}}` 로 Title 필터 시 오류
**Why it happens:** Title은 title 타입이지 rich_text가 아님
**How to avoid:** `filter={"property": "Title", "title": {"contains": ...}}`
**Warning signs:** Notion API ValidationError 또는 결과 0개

---

## Code Examples

### DB 생성 전체 예시
```python
# Source: Notion API docs 2025-09-03, databases.create endpoint (api_endpoints.py 직접 확인)
response = client.databases.create(
    parent={"type": "page_id", "page_id": "parent-page-uuid"},
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
                }
            },
            "URL": {"type": "url", "url": {}},
            "Authors": {"type": "rich_text", "rich_text": {}},
        }
    }
)
database_id = response["id"]
```

### DOI 기반 중복 검사
```python
# Source: Notion API docs 2025-09-03, data_sources.query endpoint
# data_source_id: databases.retrieve(db_id)["data_sources"][0]["id"]
result = client.data_sources.query(
    data_source_id,
    filter={"property": "DOI", "rich_text": {"equals": "10.1021/acs.xxxx"}},
    page_size=1,
)
is_duplicate = len(result["results"]) > 0
```

### 페이지 저장
```python
# Source: Notion API docs 2025-09-03, pages.create endpoint
client.pages.create(
    parent={"database_id": database_id},
    properties={
        "Title": {"title": [{"type": "text", "text": {"content": "논문 제목"}}]},
        "DOI": {"rich_text": [{"type": "text", "text": {"content": "10.1021/..."}}]},
        "Journal": {"select": {"name": "JACS"}},
        "Date": {"date": {"start": "2025-01-15"}},
        "Status": {"select": {"name": "대기중"}},
        "URL": {"url": "https://pubs.acs.org/doi/..."},
        "Authors": {"rich_text": [{"type": "text", "text": {"content": "Kim, J., Lee, S."}}]},
    }
)
```

### rate_limited 에러 코드 확인
```python
# Source: errors.py 직접 확인 (APIErrorCode.RateLimited = "rate_limited")
from notion_client import APIResponseError
try:
    client.pages.create(...)
except APIResponseError as e:
    if e.code == "rate_limited":  # 또는 e.status == 429
        time.sleep(1)
        # 재시도
    else:
        logging.warning(f"Notion API 오류: {e}")
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `databases.query(database_id)` | `data_sources.query(data_source_id)` | 2025-09-03 | discovery 단계 추가 필요 |
| `databases.create(properties=...)` | `databases.create(initial_data_source={"properties": ...})` | 2025-09-03 | 스키마 위치 변경 |
| Notion API 2022-06-28 | Notion API 2025-09-03 | notion-client 3.0.0 | multi-source database 지원 |

**Deprecated/outdated:**
- `databases.query`: notion-client 3.0.0 DatabasesEndpoint에 존재하지 않음. 구 버전 예제 코드나 블로그 참고 시 주의.
- 구버전 `databases.create(properties=...)` 패턴: initial_data_source 없이 properties만 전달하는 방식.

---

## Open Questions

1. **DB 없이 data_source_id만 있는 경우**
   - What we know: NOTION_DATABASE_ID가 있으면 기존 DB 사용 (D-08)
   - What's unclear: 기존 DB retrieve 시 data_sources 배열이 항상 비어있지 않은지 (단일 소스 DB 기준)
   - Recommendation: retrieve 후 `data_sources` 배열 길이 0 체크 방어 코드 추가

2. **create_paper_db의 parent_page_id 획득**
   - What we know: D-07은 parent_page_id를 인자로 받음
   - What's unclear: 실제 호출 시 어디서 parent_page_id를 얻는지 (환경변수? Notion workspace root?)
   - Recommendation: NOTION_PARENT_PAGE_ID 환경변수 추가 또는 함수 인자로 수동 전달 — Claude's Discretion 영역

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| notion-client | Notion DB CRUD | ✓ | 3.0.0 | — |
| Python pytest | TDD 테스트 | ✓ | 8.3.5 | — |
| pytest-mock / unittest.mock | API mock | ✓ | 3.14.0 | — |
| NOTION_TOKEN (.env) | 인증 | (런타임 확인 필요) | — | 테스트에서 mock |
| NOTION_DATABASE_ID (.env) | 기존 DB 참조 | (런타임 확인 필요) | — | create_paper_db로 신규 생성 |

**Missing dependencies with no fallback:** 없음
**Missing dependencies with fallback:** NOTION_DATABASE_ID 미설정 시 create_paper_db 경로 사용

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 |
| Config file | 없음 (pytest 기본 설정 사용) |
| Quick run command | `pytest tests/test_notion_client.py -x -q` |
| Full suite command | `pytest tests/ -x -q` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| NOTION-01 | create_paper_db가 올바른 스키마로 databases.create 호출 | unit | `pytest tests/test_notion_client.py::test_create_paper_db -x` | Wave 0 생성 |
| NOTION-01 | NOTION_DATABASE_ID 없을 때 create_paper_db 경로 분기 | unit | `pytest tests/test_notion_client.py::test_get_or_create_db_creates_new -x` | Wave 0 생성 |
| NOTION-02 | save_paper가 PaperMetadata를 올바른 properties로 변환해 pages.create 호출 | unit | `pytest tests/test_notion_client.py::test_save_paper_creates_page -x` | Wave 0 생성 |
| NOTION-02 | save_papers가 리스트의 각 논문에 save_paper 호출 | unit | `pytest tests/test_notion_client.py::test_save_papers_batch -x` | Wave 0 생성 |
| NOTION-03 | DOI 있는 논문: data_sources.query로 중복 검사 후 중복 시 스킵 | unit | `pytest tests/test_notion_client.py::test_save_paper_skips_duplicate_doi -x` | Wave 0 생성 |
| NOTION-03 | DOI 없는 논문: 제목 기반 중복 검사 | unit | `pytest tests/test_notion_client.py::test_save_paper_skips_duplicate_title -x` | Wave 0 생성 |
| NOTION-03 | rate_limited 에러 시 1초 후 재시도 | unit | `pytest tests/test_notion_client.py::test_rate_limit_retry -x` | Wave 0 생성 |

### Sampling Rate
- **Per task commit:** `pytest tests/test_notion_client.py -x -q`
- **Per wave merge:** `pytest tests/ -x -q`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_notion_client.py` — NOTION-01, NOTION-02, NOTION-03 커버
  - test_create_paper_db, test_get_or_create_db_creates_new, test_get_or_create_db_uses_existing
  - test_save_paper_creates_page, test_save_paper_skips_duplicate_doi, test_save_paper_skips_duplicate_title
  - test_save_papers_batch, test_rate_limit_retry, test_api_error_warning_and_skip

---

## Project Constraints (from CLAUDE.md)

- **Tech Stack**: Python 기반 (notion-client 공식 SDK)
- **No AI**: 정규식/직접 API 호출 기반, AI 없음
- **외부 API 키 하드코딩 금지**: NOTION_TOKEN은 .env에서만 참조
- **TDD**: pytest + mock 사용, Phase 1-3에서 확립된 패턴 따름
- **플랫 구조**: notion_client.py를 프로젝트 루트에 생성 (src/ 없음)
- **에러 패턴**: logging.warning + 스킵 (Phase 3 패턴 동일)
- **한국어 주석**: 이해하기 어려운 코드에 한국어 주석 추가

---

## Sources

### Primary (HIGH confidence)
- `notion_client/api_endpoints.py` 직접 열람 — DatabasesEndpoint (create, retrieve, update만 존재, query 없음), DataSourcesEndpoint (query 있음), PagesEndpoint (create, retrieve, update, move)
- `notion_client/client.py` 직접 열람 — notion_version: "2025-09-03", httpx 기반
- `notion_client/errors.py` 직접 열람 — APIErrorCode.RateLimited = "rate_limited", APIResponseError 생성자
- Notion API 공식 문서 (https://developers.notion.com/reference/post-database-query) — databases/query deprecated 확인
- Notion API 업그레이드 가이드 (https://developers.notion.com/docs/upgrade-guide-2025-09-03) — data_source_id discovery 패턴
- Notion API 공식 문서 (https://developers.notion.com/reference/create-a-database) — initial_data_source 구조
- Notion API 공식 문서 (https://developers.notion.com/reference/post-page) — pages.create properties 구조

### Secondary (MEDIUM confidence)
- Notion API 공식 문서 (https://developers.notion.com/reference/query-a-data-source) — data_sources.query filter 구조

### Tertiary (LOW confidence)
- 없음

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — SDK 소스 직접 확인
- Architecture: HIGH — Notion 공식 API 문서 + SDK 소스 확인
- Pitfalls: HIGH — databases.query 미존재 직접 확인, Notion API 변경 공식 문서 확인

**Research date:** 2026-04-05
**Valid until:** 2026-05-05 (notion-client major 버전 변경 시 재검토)
