---
phase: 01-auth-env-setup
plan: 01
subsystem: auth
tags: [gmail, oauth, google-api-python-client, python-dotenv, pytest, venv]

# Dependency graph
requires: []
provides:
  - Gmail OAuth 2.0 인증 모듈 (auth.py / get_gmail_service())
  - 프로젝트 환경 설정 (requirements.txt, config.py, .env.example, .gitignore)
  - venv + 전체 의존성 설치
  - Gmail API 연결 검증 스크립트 (verify_gmail.py)
  - 단위 테스트 인프라 (pytest, conftest.py)
affects: [02-notion-setup, 03-gmail-parser, 04-pipeline-integration, 05-deployment]

# Tech tracking
tech-stack:
  added:
    - google-auth==2.49.1
    - google-auth-oauthlib==1.3.1
    - google-api-python-client==2.193.0
    - notion-client==3.0.0
    - python-dotenv==1.2.2
    - pytest==8.3.5
    - pytest-mock==3.14.0
  patterns:
    - "config.py 단일 모듈에서 python-dotenv로 환경변수 로드 후 상수 노출"
    - "auth.py: token.json 체크 → 갱신 → OAuth flow 순서의 인증 패턴"
    - "TDD: test → RED → 구현 → GREEN 순서 적용"

key-files:
  created:
    - requirements.txt
    - config.py
    - .env.example
    - .gitignore
    - auth.py
    - verify_gmail.py
    - tests/__init__.py
    - tests/conftest.py
    - tests/test_gmail_auth.py
  modified: []

key-decisions:
  - "pip + requirements.txt + venv 조합으로 패키지 관리 (오라클 클라우드 배포 호환)"
  - "플랫 구조: 루트에 auth.py, config.py 등 배치 (src/ 없음)"
  - "gmail.readonly 스코프로 시작 (Phase 2에서 필요 시 gmail.modify 확장)"
  - "token.json 자동 갱신: expired + refresh_token 조건 체크 후 creds.refresh(Request())"

patterns-established:
  - "환경변수 패턴: os.getenv('KEY', 'default') 형식으로 기본값 포함"
  - "OAuth 패턴: token.json 로드 → 유효성 검사 → 갱신/재인증 → build() 반환"

requirements-completed: [AUTH-01]

# Metrics
duration: 5min
completed: 2026-04-03
---

# Phase 01 Plan 01: Gmail OAuth 인증 환경 구성 Summary

**google-auth-oauthlib 기반 Gmail OAuth 2.0 인증 모듈(token.json 자동갱신 포함) + venv 환경 구성 + pytest TDD 4개 테스트 통과 + 실제 Gmail OAuth 브라우저 인증 완료 (14개 라벨 확인)**

## Performance

- **Duration:** 5 min
- **Started:** 2026-04-03T07:47:33Z
- **Completed:** 2026-04-03T07:54:06Z
- **Tasks:** 3 of 3 completed
- **Files modified:** 9

## Accomplishments

- requirements.txt, config.py, .env.example, .gitignore로 프로젝트 기반 구조 완성
- .venv에 google-api-python-client, notion-client, pytest 등 7개 패키지 설치
- auth.py의 get_gmail_service()가 토큰 로드/만료갱신/OAuth flow를 완전히 처리
- pytest 단위 테스트 4개 전체 통과 (TDD 방식 적용)
- Google Cloud Console에서 Gmail API 활성화 및 OAuth credentials.json 발급 완료
- 브라우저 OAuth 인증 완료 → token.json 생성 → verify_gmail.py로 Gmail API 연결 확인 (14개 라벨)

## Task Commits

1. **Task 1: 프로젝트 스캐폴딩 생성** - `a621452` (chore)
2. **Task 2 RED: Gmail OAuth 테스트 추가** - `6ad3e02` (test)
3. **Task 2 GREEN: Gmail OAuth 인증 모듈 구현** - `bb605e1` (feat)
4. **Task 3: Gmail OAuth 브라우저 인증** - human-action (사용자 완료, token.json 생성됨)

## Files Created/Modified

- `requirements.txt` - Python 의존성 7개 고정 버전 명시
- `config.py` - python-dotenv로 환경변수 로드, GMAIL_SCOPES/경로/Notion 설정 상수 노출
- `.env.example` - 환경변수 템플릿 (GMAIL, Notion, CHECK_INTERVAL_HOURS)
- `.gitignore` - token.json, credentials.json, .env, __pycache__, .venv 제외
- `auth.py` - get_gmail_service(): token.json 로드 → 만료갱신 → OAuth flow → build() 반환
- `verify_gmail.py` - labels().list() 호출로 Gmail API 연결 확인
- `tests/__init__.py` - 테스트 패키지 초기화
- `tests/conftest.py` - mock_env 픽스처 (테스트용 환경변수 설정)
- `tests/test_gmail_auth.py` - 4개 단위 테스트

## Decisions Made

- pip + venv 조합 사용 (오라클 클라우드 Ubuntu 배포 호환성 우선)
- gmail.readonly 스코프로 시작, 필요 시 Phase 2에서 확장
- token.json을 프로젝트 루트에 저장, SCP로 서버 복사하는 워크플로우

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Completed

**Task 3: Gmail OAuth 브라우저 인증 완료.**

완료된 작업:
1. Google Cloud Console에서 프로젝트 생성 및 Gmail API 활성화
2. OAuth 동의 화면 설정 및 테스트 사용자 등록
3. Desktop App OAuth credential 생성 및 credentials.json 다운로드
4. verify_gmail.py 실행 → 브라우저 OAuth 인증 완료 → token.json 생성
5. Gmail API 연결 확인: 14개 라벨 조회 성공

## Next Phase Readiness

- 프로젝트 환경 및 Gmail OAuth 인증 완전 완성
- token.json 생성 완료, verify_gmail.py로 실제 연결 확인됨
- Phase 01-02 (Notion 인증 설정) 진행 가능

## Self-Check: PASSED

All expected files present. All commits verified.

---
*Phase: 01-auth-env-setup*
*Completed: 2026-04-03*
