---
phase: 04-notion
plan: 02
subsystem: database
tags: [notion, notion-client, python, integration-test, deduplication]

# Dependency graph
requires:
  - phase: 04-notion/04-01
    provides: notion_client_mod.py (create_paper_db, get_or_create_db, save_paper, save_papers)
  - phase: 01-auth-env-setup
    provides: notion_auth.get_notion_client() 재사용
  - phase: 02-mail-detection
    provides: models.PaperMetadata 데이터 모델
provides:
  - notion_client_mod.py -- 실제 Notion API로 검증된 Notion DB CRUD 모듈
  - Notion "get-ASAP Papers" DB (d26e7ce9-b99a-4d37-beff-e2a065339a24) 생성 확인
affects: [05-integration, pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "통합 테스트에서 NOTION_PARENT_PAGE_ID를 임시 환경변수로 주입하여 DB 자동 생성 검증"
    - "notion-client 3.0.0: data_sources.query가 실제 API에서도 정상 동작 확인"
    - "em dash (U+2014) 대신 하이픈(-) 사용 -- Windows cp949 콘솔 호환성"

key-files:
  created: []
  modified:
    - notion_client_mod.py

key-decisions:
  - "em dash (U+2014) -> 하이픈(-) 변경: Windows 콘솔(cp949) UnicodeEncodeError 방지 (Rule 1 - Bug)"
  - "NOTION_DATABASE_ID를 .env에 저장 권장: 신규 생성된 DB ID를 재사용하기 위해 (d26e7ce9-b99a-4d37-beff-e2a065339a24)"

patterns-established:
  - "Pattern: 통합 테스트 시 임시 환경변수 주입으로 DB 자동 생성 경로 검증"

requirements-completed: [NOTION-01, NOTION-02, NOTION-03]

# Metrics
duration: 4min
completed: 2026-04-04
---

# Phase 4 Plan 02: Notion API 통합 테스트 Summary

**실제 Notion API 호출로 DB 생성/페이지 저장/DOI 기반 중복 방지 동작 검증 완료, em dash 로그 버그 자동 수정**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-04T17:01:40Z
- **Completed:** 2026-04-04T17:05:42Z
- **Tasks:** 1 (Task 1 auto) + Task 2 (checkpoint)
- **Files modified:** 1

## Accomplishments

- 실제 Notion API로 6단계 통합 테스트 전체 통과 (연결 확인, DB 생성, data_source_id 획득, 논문 저장, 중복 방지, 배치 저장)
- Notion "get-ASAP Papers" DB 자동 생성 확인 (DB ID: d26e7ce9-b99a-4d37-beff-e2a065339a24)
- em dash 로그 UnicodeEncodeError 버그 자동 수정 (Rule 1)
- 86개 기존 단위 테스트 회귀 없음

## Task Commits

각 태스크를 원자적으로 커밋:

1. **Task 1: 실제 Notion API 통합 테스트 스크립트 작성 및 실행** - `24bb496` (fix)

**Plan metadata:** TBD (docs: complete plan)

## Files Created/Modified

- `notion_client_mod.py` -- em dash (U+2014) -> 하이픈(-) 변경 (Windows cp949 호환성 버그 수정)

## Decisions Made

- **em dash -> 하이픈 변경**: save_papers의 진행률 로그 메시지에서 `—` (U+2014 em dash)를 `-`(하이픈)으로 교체. Windows cp949 콘솔에서 UnicodeEncodeError가 발생함. Ubuntu 배포 환경에서는 문제없지만 개발 환경 호환성 위해 수정.
- **생성된 DB ID 기록**: 통합 테스트로 생성된 "get-ASAP Papers" DB ID(d26e7ce9-b99a-4d37-beff-e2a065339a24)를 .env의 NOTION_DATABASE_ID에 저장하도록 사용자에게 안내.

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] em dash 로그 메시지 UnicodeEncodeError 수정**
- **Found during:** Task 1 (통합 테스트 실행 중)
- **Issue:** `notion_client_mod.py` line 222의 `f"저장 중: {i + 1}/{total} — {paper.title[:50]}"` 에서 em dash(U+2014)가 Windows 콘솔(cp949) 인코딩에서 UnicodeEncodeError 발생. API 호출은 성공했으나 로그 출력이 실패함.
- **Fix:** em dash `—` -> 하이픈 `-` 교체
- **Files modified:** `notion_client_mod.py` (line 222)
- **Verification:** 86개 단위 테스트 통과 확인
- **Committed in:** `24bb496` (Task 1 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Windows 개발 환경 호환성 향상. 기능에는 영향 없음.

## Issues Encountered

- **NOTION_PARENT_PAGE_ID 미설정**: .env에 NOTION_PARENT_PAGE_ID가 없어 get_or_create_db()가 ValueError를 발생시킬 수 있었음. 통합 테스트에서 임시로 Notion 검색 API로 "get-ASAP" 페이지 ID를 찾아 환경변수에 주입하여 해결.
- **통합 테스트 DB 재사용**: 통합 테스트 실행마다 새 DB가 생성될 수 있음. .env에 NOTION_DATABASE_ID를 설정하면 기존 DB를 재사용함.

## User Setup Required

통합 테스트로 생성된 Notion DB를 계속 사용하려면:

1. `.env` 파일에 추가:
   ```
   NOTION_DATABASE_ID=d26e7ce9-b99a-4d37-beff-e2a065339a24
   NOTION_PARENT_PAGE_ID=337dbb00-cf2e-8014-a27f-cf5a04e68350
   ```
2. 테스트 데이터 삭제: Notion "get-ASAP Papers" DB에서 테스트 페이지 3개 수동 삭제

## Next Phase Readiness

- notion_client_mod.py 실제 API 검증 완료: Phase 5 (통합 파이프라인)에서 즉시 사용 가능
- 생성된 DB ID(d26e7ce9-...)를 .env에 저장하면 Phase 5에서 DB 재사용 가능

## Known Stubs

없음 -- 모든 함수가 실제 Notion API로 동작 검증됨.

## Self-Check: PASSED

- notion_client_mod.py: FOUND
- 04-02-SUMMARY.md: FOUND
- Commit 24bb496 (Task 1 fix): FOUND

---
*Phase: 04-notion*
*Completed: 2026-04-04*
