# Phase 4: Notion 통합 및 중복 방지 - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning
**Source:** Auto-mode (recommended defaults selected)

<domain>
## Phase Boundary

추출된 논문 메타데이터(PaperMetadata)를 Notion DB에 저장하고, DOI 기반으로 동일 논문의 중복 저장을 방지한다. Notion DB 스키마 생성과 CRUD 기능을 구현한다.

</domain>

<decisions>
## Implementation Decisions

### Notion DB 스키마 설계
- **D-01:** PaperMetadata 필드를 Notion DB 속성으로 매핑:
  - title → Title 속성 (Notion 기본 제목)
  - doi → Rich Text 속성 (중복 검색 필터에 사용)
  - journal → Select 속성 (저널명 자동 옵션화)
  - date → Date 속성 (ISO 형식)
  - 상태 → Select 속성 (기본값: "대기중")
  - url → URL 속성 (선택, 있으면 저장)
  - authors → Rich Text 속성 (선택, 콤마 구분)
- **D-02:** DB 제목은 "get-ASAP Papers" 또는 사용자 지정 가능

### 중복 방지 전략
- **D-03:** 저장 전 `databases.query(filter={doi=X})`로 기존 논문 확인 — DOI 일치하면 스킵 + 로그
- **D-04:** DOI가 비어있는 논문은 제목 기반 중복 검사 (title 필드 contains 필터)
- **D-05:** 중복 발견 시 logging.info로 기록 후 건너뜀 (덮어쓰기 없음)

### 모듈 구조
- **D-06:** notion_client.py 모듈에 Notion DB CRUD 기능 통합
- **D-07:** `create_paper_db(parent_page_id)` — 최초 1회 DB 생성 함수
- **D-08:** `NOTION_DATABASE_ID` 환경변수가 있으면 기존 DB 사용, 없으면 신규 생성
- **D-09:** `save_paper(paper: PaperMetadata)` — 단일 논문 저장 함수
- **D-10:** `save_papers(papers: list[PaperMetadata])` — 배치 저장 + 중복 검사 통합

### 에러 핸들링
- **D-11:** Notion API 실패 시 logging.warning + 스킵 (Phase 3 파서와 동일 패턴)
- **D-12:** API rate limit(429) 시 1회 sleep(1초) 후 재시도, 재실패 시 스킵

### Claude's Discretion
- Notion API 페이지네이션 처리 방식
- DB 생성 시 parent page 선택 로직
- 배치 저장 시 진행률 출력 여부
- .env.example에 NOTION_DATABASE_ID 추가 여부

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project-level
- `.planning/PROJECT.md` — 프로젝트 비전, 제약사항
- `.planning/REQUIREMENTS.md` — NOTION-01, NOTION-02, NOTION-03 요구사항 정의

### Prior Phases
- `.planning/phases/01-auth-env-setup/01-CONTEXT.md` — Notion 인증 결정 (D-07~D-10)
- `.planning/phases/03-parser-impl/03-CONTEXT.md` — PaperMetadata 구조, 파서 출력 형식

### Existing Code (필수 참고)
- `notion_auth.py` — get_notion_client(), verify_notion_connection() 구현
- `config.py` — NOTION_TOKEN, NOTION_DATABASE_ID 환경변수 정의
- `models.py` — PaperMetadata dataclass (title, doi, journal, date, authors, url)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `notion_auth.py`: get_notion_client() — Notion Client 객체 반환 (Phase 1에서 구현)
- `config.py`: NOTION_TOKEN, NOTION_DATABASE_ID 환경변수 이미 정의
- `models.py`: PaperMetadata dataclass — 파서 출력이자 Notion 저장 입력

### Established Patterns
- TDD (pytest + mock) — Phase 1-3에서 확립
- logging.warning + 계속 진행 — Phase 3 에러 핸들링 패턴
- 플랫 프로젝트 구조 — notion_client.py를 루트에 생성

### Integration Points
- Phase 3 파서 출력 `list[PaperMetadata]` → Phase 4 `save_papers()` 입력
- `notion_auth.get_notion_client()` → notion_client.py에서 호출
- `config.NOTION_DATABASE_ID` → DB ID 설정
- Phase 5 배포에서 전체 파이프라인 조합 시 사용

</code_context>

<specifics>
## Specific Ideas

- notion-client SDK의 databases.create, pages.create, databases.query 엔드포인트 사용
- DOI 중복 검사는 filter의 rich_text.equals 사용
- 상태 Select 옵션: "대기중", "읽음", "관심", "스킵" (후속 시스템에서 사용)

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 04-notion*
*Context gathered: 2026-04-05 via auto-mode*
