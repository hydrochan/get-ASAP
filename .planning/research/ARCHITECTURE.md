# Architecture Research

**Domain:** Gmail-to-Notion 자동화 파이프라인
**Researched:** 2026-04-03
**Confidence:** HIGH

## Recommended Architecture Pattern

단일 책임 파이프라인 패턴. 각 컴포넌트는 명확한 입출력 인터페이스를 가지며, 출판사별 파싱 로직은 플러그인 방식으로 분리한다. 전체 흐름: Gmail 수신 → 파싱 → Notion 저장.

## Component Architecture

```
┌─────────────────────────────────────────────┐
│                  main.py (오케스트레이터)       │
│  - cron 진입점                               │
│  - 전체 파이프라인 조율                        │
└──────────────┬──────────────────────────────┘
               │
       ┌───────▼────────┐
       │  gmail_client  │
       │  - OAuth 인증   │
       │  - 메일 목록 조회│
       │  - 메일 본문 읽기│
       └───────┬────────┘
               │ 원시 메일 데이터
       ┌───────▼────────┐
       │ parser_router  │
       │  - 출판사 판별  │
       │  - 파서 선택    │
       └───────┬────────┘
               │
    ┌──────────┼──────────┐
    │          │          │
┌───▼──┐  ┌───▼──┐  ┌────▼──┐
│acs_  │  │rsc_  │  │wiley_ │  ... (출판사별 파서)
│parser│  │parser│  │parser │
└───┬──┘  └───┬──┘  └────┬──┘
    └──────────┼──────────┘
               │ 정규화된 논문 데이터
               │ {title, doi, journal, date}
       ┌───────▼────────┐
       │ notion_client  │
       │  - DB 조회      │
       │  - 중복 체크    │
       │  - 페이지 생성  │
       └────────────────┘
```

## Major Components

### 1. gmail_client.py
**책임:** Gmail API 인증 및 메일 데이터 접근

- `authenticate()` — credentials.json으로 OAuth, token.json 저장/갱신
- `get_asap_emails(since_date)` — 레이블/발신자 필터로 ASAP 메일 목록 조회
- `get_email_body(message_id)` — Base64 디코딩, HTML/텍스트 파트 추출
- `mark_processed(message_id)` — 처리 완료 메일 추적 (선택사항: 로컬 파일)

**핵심 결정:** `message.list`의 `q` 파라미터로 `from:acs OR from:rsc` 필터링 → 불필요한 API 호출 최소화

### 2. parsers/ (디렉토리)
**책임:** 출판사별 메일에서 논문 메타데이터 추출

```
parsers/
├── __init__.py       # 공통 인터페이스 정의
├── base_parser.py    # 추상 기반 클래스
├── acs_parser.py     # ACS Publications
├── rsc_parser.py     # Royal Society of Chemistry
├── wiley_parser.py   # Wiley Online Library
├── nature_parser.py  # Nature Publishing Group
├── elsevier_parser.py# Elsevier/ScienceDirect
└── science_parser.py # AAAS Science
```

**공통 인터페이스:**
```python
class BaseParser:
    def can_parse(self, email: dict) -> bool: ...
    def parse(self, email: dict) -> list[PaperMetadata]: ...
```

**PaperMetadata 스키마:**
```python
@dataclass
class PaperMetadata:
    title: str
    doi: str
    journal: str
    published_date: str  # ISO 8601
    source_email_id: str
```

### 3. notion_client.py
**책임:** Notion API 통신 및 DB 관리

- `ensure_database(parent_page_id)` — DB 존재 확인, 없으면 신규 생성
- `is_duplicate(doi)` — DOI로 기존 페이지 쿼리
- `create_paper_page(metadata)` — 논문 페이지 생성, 상태="대기중" 기본값

**Notion DB 스키마:**
| Property | Type | Notes |
|----------|------|-------|
| 제목 | title | 논문 제목 |
| DOI | rich_text | 고유 식별자 |
| 저널 | select | 출판사/저널명 |
| 발행일 | date | ISO 8601 |
| 상태 | select | 기본값: "대기중" |

### 4. main.py (진입점)
**책임:** 파이프라인 오케스트레이션

```python
def run():
    emails = gmail_client.get_asap_emails(since_hours=24)
    for email in emails:
        parser = parser_router.select(email)
        if not parser:
            log.warning(f"No parser for email {email['id']}")
            continue
        papers = parser.parse(email)
        for paper in papers:
            if not notion_client.is_duplicate(paper.doi):
                notion_client.create_paper_page(paper)
```

### 5. config.py + .env
**책임:** 환경 변수 및 설정 중앙화

```
NOTION_TOKEN=...
NOTION_DATABASE_ID=...  # 최초 실행 후 저장
GMAIL_CREDENTIALS_PATH=./credentials.json
GMAIL_TOKEN_PATH=./token.json
CHECK_INTERVAL_HOURS=24
```

## Data Flow

```
Gmail API
    │
    │ raw email (base64 encoded, HTML/text parts)
    ▼
gmail_client.get_email_body()
    │
    │ decoded HTML/text string
    ▼
parser_router → acs_parser.parse()
    │
    │ PaperMetadata(title, doi, journal, date)
    ▼
notion_client.is_duplicate(doi)
    │
    │ False → create / True → skip
    ▼
Notion Database
```

## Key Patterns

1. **파서 플러그인 패턴:** 새 출판사 추가 시 새 파일만 추가, main.py 수정 불필요
2. **멱등성(Idempotency):** 동일 DOI 재실행 시 중복 생성 안 됨 — 크론 재실행 안전
3. **실패 격리:** 한 메일 파싱 실패가 전체 파이프라인을 중단시키지 않음 (try/except per email)
4. **지연 파서 개발:** base_parser의 인터페이스만 정의하고, 실제 메일 수신 후 파서 구현

## Deployment Architecture

```
오라클 클라우드 Ubuntu
├── ~/get-ASAP/
│   ├── main.py
│   ├── gmail_client.py
│   ├── notion_client.py
│   ├── parsers/
│   ├── credentials.json  # OAuth 앱 자격증명
│   ├── token.json        # 갱신 가능 액세스 토큰
│   ├── .env              # Notion 토큰 등
│   └── venv/
│
└── crontab: */30 * * * * cd ~/get-ASAP && ./venv/bin/python main.py >> logs/run.log 2>&1
```

## Sources

- Gmail API Python Quickstart (공식) — OAuth 흐름, message API
- Notion API 공식 문서 — database.query, pages.create
- 도메인 지식 — 학술 출판사 ASAP 메일 패턴

---
*Architecture research for: Gmail-to-Notion 자동화 파이프라인*
*Researched: 2026-04-03*
