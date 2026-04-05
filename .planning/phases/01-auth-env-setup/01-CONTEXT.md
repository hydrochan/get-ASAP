# Phase 1: 인증 및 환경 설정 - Context

**Gathered:** 2026-04-03
**Status:** Ready for planning

<domain>
## Phase Boundary

Gmail OAuth 2.0과 Notion Integration Token으로 양쪽 API에 안정적으로 연결되는 인증 기반을 구축한다.
프로젝트 디렉토리 구조, 패키지 관리, 환경 변수 구성, 인증 검증 스크립트까지 포함.

</domain>

<decisions>
## Implementation Decisions

### 프로젝트 구조
- **D-01:** pip + requirements.txt + venv 조합으로 패키지 관리 (오라클 클라우드 Ubuntu 배포 호환성 우선)
- **D-02:** 플랫 구조 — 루트에 main.py, auth.py, gmail_client.py, notion_client.py, config.py + parsers/ 디렉토리 (src/ 패키지 없이 단순하게)

### OAuth 인증 설정
- **D-03:** Google Cloud Console에서 Desktop App 타입 OAuth credential 발급
- **D-04:** 로컬 PC에서 브라우저 인증 → token.json 생성 → SCP로 오라클 서버에 복사하는 워크플로우
- **D-05:** gmail.readonly scope로 시작 (Phase 2에서 라벨 부여 필요 시 gmail.modify로 확장)
- **D-06:** token.json 자동 갱신 구현 (google-auth-oauthlib 활용)

### 환경 변수 구성
- **D-07:** 단일 .env 파일에 인증 정보 + 설정 통합 저장
- **D-08:** .env 항목: GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH, NOTION_TOKEN, NOTION_DATABASE_ID, CHECK_INTERVAL_HOURS

### 검증 스크립트
- **D-09:** verify_gmail.py — Gmail 메일박스 조회로 인증 성공 확인
- **D-10:** verify_notion.py — Notion 워크스페이스 접근으로 인증 성공 확인
- 개별 분리하여 각 API를 독립적으로 검증 가능

### Claude's Discretion
- token.json 자동 갱신 구현 방식의 세부 사항
- config.py에서 python-dotenv 사용 방식
- .gitignore 구성 (credentials.json, token.json, .env 제외)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project-level
- `.planning/PROJECT.md` — 프로젝트 비전, 제약사항, 기술 스택 결정
- `.planning/REQUIREMENTS.md` — AUTH-01, AUTH-02 요구사항 정의

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- 없음 (신규 프로젝트, CLAUDE.md만 존재)

### Established Patterns
- 없음 (Phase 1에서 패턴 확립 예정)

### Integration Points
- Gmail API: google-api-python-client, google-auth-oauthlib
- Notion API: notion-client (Python)
- 환경 변수: python-dotenv

</code_context>

<specifics>
## Specific Ideas

- credentials.json은 Google Cloud Console에서 다운로드하여 프로젝트 루트에 배치
- token.json은 최초 로컬 인증 시 자동 생성되며, 이후 SCP로 서버에 복사
- .env.example 파일을 제공하여 필요한 환경 변수 목록을 문서화

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 01-auth-env-setup*
*Context gathered: 2026-04-03*
