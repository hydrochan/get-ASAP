# Phase 2: 메일 감지 프레임워크 - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Gmail API로 출판사 ASAP 메일을 필터링하고, historyId 기반 증분 동기화로 새 메일만 처리하며, 처리 완료 메일에 라벨을 부여한다. 파서 플러그인 구조(Strategy Pattern)를 구축하여 새 출판사 추가가 파일 하나로 가능하게 한다.

</domain>

<decisions>
## Implementation Decisions

### 메일 필터링 전략
- **D-01:** 발신자 이메일 기반 Gmail API 쿼리로 ASAP 메일 필터링 (from:alerts@acs.org OR from:notify@science.org 등)
- **D-02:** publishers.json 외부 파일에 출판사별 설정 저장 (발신자 이메일, 출판사명, 대표 저널 등). 코드 수정 없이 출판사 추가/수정 가능

### 증분 동기화 + 처리 마킹
- **D-03:** historyId 기반 증분 동기화 — Gmail API history.list로 마지막 실행 이후 변경된 메일만 가져옴. state.json에 historyId 영속화
- **D-04:** 처리 완료 메일에 라벨 부여 — "get-ASAP-processed" 라벨 생성 및 부여. Gmail에서 시각적 확인 가능
- **D-05:** gmail.readonly → gmail.modify로 scope 확장 필요 — Phase 1에서 생성한 token.json 삭제 후 재인증 필요. auth.py의 GMAIL_SCOPES 수정

### 저널명 추론 로직
- **D-06:** publishers.json에 발신자→출판사+저널 매핑 정의. 메일 제목에서 구체적 저널명을 정규식으로 추가 추출

### 파서 플러그인 구조
- **D-07:** parsers/ 디렉토리 자동 디스커버리 — BaseParser 서브클래스를 자동 스캔하여 등록. 파일 추가만으로 새 파서 등록
- **D-08:** PaperMetadata dataclass 반환 — title, doi, journal, date 필드. 타입 안전하고 명확한 데이터 구조

### Claude's Discretion
- state.json의 상세 구조 (historyId 외 추가 필드)
- publishers.json의 상세 스키마 (필수/선택 필드)
- BaseParser 추상 메서드 인터페이스 상세 설계
- 라벨 생성 API 호출 방식 (labels.create)
- 메일 본문 디코딩 방식 (base64, multipart 처리)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project-level
- `.planning/PROJECT.md` — 프로젝트 비전, 제약사항, 기술 스택 결정
- `.planning/REQUIREMENTS.md` — MAIL-01~03, PARSE-04~05 요구사항 정의

### Prior Phase
- `.planning/phases/01-auth-env-setup/01-CONTEXT.md` — Phase 1 인증 결정사항 (D-05 scope 확장 관련)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `auth.py`: `get_gmail_service()` — Gmail API 서비스 객체 반환 (Phase 2에서 재사용)
- `config.py`: .env 로딩 패턴 확립, GMAIL_SCOPES 정의 (scope 수정 필요)
- `verify_gmail.py`: Gmail 연결 검증 스크립트 (패턴 참고)

### Established Patterns
- python-dotenv 기반 환경변수 관리
- google-auth-oauthlib 기반 OAuth 인증 + 토큰 자동갱신
- TDD (pytest, mock 기반 단위 테스트)
- 플랫 프로젝트 구조

### Integration Points
- `auth.py`의 `get_gmail_service()` → gmail_client.py에서 호출
- `config.py`의 GMAIL_SCOPES → gmail.modify로 변경
- `parsers/` 디렉토리 신규 생성 → BaseParser + 자동 디스커버리

</code_context>

<specifics>
## Specific Ideas

- gmail.modify scope 확장 시 기존 token.json 삭제 후 재인증 워크플로우 안내 필요
- publishers.json 예시: `{"acs": {"sender": "alerts@acs.org", "name": "ACS", "journals": ["JACS", "ACS Nano", ...]}}`
- PaperMetadata: `@dataclass` with `title: str, doi: str, journal: str, date: str`

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-mail-detection*
*Context gathered: 2026-04-03*
