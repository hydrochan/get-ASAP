---
phase: 01-auth-env-setup
plan: 02
subsystem: auth
tags: [notion-client, python-dotenv, notion-api, tdd, pytest]

# Dependency graph
requires:
  - phase: 01-auth-env-setup/01-01
    provides: config.py NOTION_TOKEN 로드, tests/conftest.py mock_env 픽스처
provides:
  - notion_auth.py: get_notion_client() + verify_notion_connection() 구현
  - verify_notion.py: 독립 실행 Notion 연결 검증 스크립트
  - tests/test_notion_auth.py: 5개 단위 테스트
affects:
  - phase 04 (notion-integration): notion_auth.get_notion_client() 직접 사용
  - phase 02 (email-processing): Notion 저장 로직에서 이 모듈 의존

# Tech tracking
tech-stack:
  added: [notion-client 2.x]
  patterns: [모듈 레벨 import 후 테스트에서 importlib.reload로 환경변수 반영, APIResponseError를 ConnectionError로 래핑]

key-files:
  created:
    - notion_auth.py
    - verify_notion.py
    - tests/test_notion_auth.py
  modified: []

key-decisions:
  - "APIResponseError 생성자 시그니처가 (code, status, message, headers, raw_body_text)임 - 테스트에서 실제 시그니처 반영 필요"
  - "notion_auth 모듈 레벨 NOTION_TOKEN import로 인해 테스트에서 importlib.reload 필수"

patterns-established:
  - "Pattern 1: 모듈 레벨 환경변수 import 후 테스트에서 reload 패턴 (config + 대상 모듈 순서로 reload)"
  - "Pattern 2: notion_client.APIResponseError를 ConnectionError로 래핑하여 상위 레이어에 깔끔한 인터페이스 제공"

requirements-completed: [AUTH-02]

# Metrics
duration: 15min
completed: 2026-04-03
---

# Phase 01 Plan 02: Notion 인증 모듈 Summary

**notion-client SDK로 NOTION_TOKEN 기반 Notion API 클라이언트 생성 및 워크스페이스 연결 검증 구현 (5개 단위 테스트 통과)**

## Performance

- **Duration:** 15 min
- **Started:** 2026-04-03T08:10:00Z
- **Completed:** 2026-04-03T08:25:00Z
- **Tasks:** 2/2 (Task 2 checkpoint:human-verify APPROVED)
- **Files modified:** 3

## Accomplishments
- notion_auth.py: get_notion_client() / verify_notion_connection() 구현
- verify_notion.py: 독립 실행 스크립트로 실제 API 연결 검증 가능
- 5개 단위 테스트 전부 통과 (token success, no-token error, connection success/failure, hardcoded-token check)

## Task Commits

1. **Task 1: Notion 인증 모듈 구현 (TDD)** - `fb3d09b` (feat)
2. **Task 2: Notion 연결 검증 및 전체 Phase 1 확인** - `b5564e4` (fix) — checkpoint:human-verify APPROVED
   - verify_notion.py 실행 성공: 워크스페이스 이름 출력 확인
   - verify_gmail.py 실행 성공: Gmail 라벨 확인
   - pytest tests/ -v: 9/9 통과
   - notion_auth.py에서 `from config import NOTION_TOKEN` → `import config` + `config.NOTION_TOKEN` 런타임 참조로 수정

**Plan metadata:** (docs commit - 아래에서 생성)

## Files Created/Modified
- `notion_auth.py` - NOTION_TOKEN으로 Client 생성, APIResponseError -> ConnectionError 래핑
- `verify_notion.py` - 독립 실행 Notion 연결 검증 스크립트
- `tests/test_notion_auth.py` - 5개 단위 테스트

## Decisions Made
- `importlib.reload`를 config -> notion_auth 순서로 수행해야 환경변수 변경이 반영됨
- APIResponseError 실제 생성자 시그니처: `(code, status, message, headers, raw_body_text)`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] 테스트 내 patch 순서 수정**
- **Found during:** Task 1 (GREEN 단계)
- **Issue:** `patch("notion_auth.Client")` 컨텍스트 안에서 `importlib.reload(notion_auth)`를 호출하면 모듈이 재임포트되어 patch가 무효화됨
- **Fix:** reload를 patch 컨텍스트 진입 전에 수행하도록 순서 변경
- **Files modified:** tests/test_notion_auth.py
- **Verification:** test_get_notion_client_success PASSED
- **Committed in:** fb3d09b

**2. [Rule 1 - Bug] APIResponseError 생성자 시그니처 수정**
- **Found during:** Task 1 (GREEN 단계)
- **Issue:** 플랜의 `APIResponseError(MagicMock(status=401), "", "")` 호출이 실제 SDK 시그니처와 불일치 (`TypeError: missing 2 required positional arguments`)
- **Fix:** 실제 시그니처 `(code, status, message, headers, raw_body_text)` 맞게 수정
- **Files modified:** tests/test_notion_auth.py
- **Verification:** test_verify_notion_connection_failure PASSED
- **Committed in:** fb3d09b

---

**Total deviations:** 2 auto-fixed (2 bugs in test code)
**Impact on plan:** 테스트 코드의 mock 사용 방식 수정. notion_auth.py 구현 로직은 플랜과 동일.

## Issues Encountered
- notion-client SDK의 APIResponseError 생성자 시그니처가 플랜에 명시된 것과 달랐음. `inspect.signature`로 확인 후 수정.

## User Setup Completed

**Task 2 (checkpoint:human-verify) — APPROVED on 2026-04-03**

사용자 완료 내역:
- Notion Integration "get-ASAP" 생성 (Internal)
- `.env`에 `NOTION_TOKEN` 추가
- `verify_notion.py` 실행 성공 — 워크스페이스 이름 출력 확인
- `verify_gmail.py` 실행 성공 — Gmail 라벨 확인
- `pytest tests/ -v` — 9/9 통과

추가 버그 수정 (commit b5564e4):
- `notion_auth.py`: 모듈 레벨 `from config import NOTION_TOKEN` 방식이 런타임 환경변수 변경을 반영하지 못하는 문제
- 수정: `import config` + `config.NOTION_TOKEN` 런타임 참조 방식으로 변경

## Next Phase Readiness
- Gmail + Notion 양쪽 API 연결 검증 완료 — Phase 1 전체 목표 달성
- notion_auth.py, auth.py 모두 완성 상태 — Phase 2/4에서 즉시 사용 가능
- Phase 2 (email-processing)로 진행 가능

## Self-Check: PASSED

- notion_auth.py: FOUND
- verify_notion.py: FOUND
- tests/test_notion_auth.py: FOUND
- .planning/phases/01-auth-env-setup/01-02-SUMMARY.md: FOUND
- Commit fb3d09b (feat): FOUND
- Commit b5564e4 (fix): FOUND
- pytest tests/: 9/9 PASSED

---
*Phase: 01-auth-env-setup*
*Completed: 2026-04-03*
