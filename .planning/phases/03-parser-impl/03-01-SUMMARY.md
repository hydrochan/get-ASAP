---
phase: 03-parser-impl
plan: "01"
subsystem: testing
tags: [gmail, fixtures, collect_samples, publishers, html]

requires:
  - phase: 02-mail-detection
    provides: "gmail_client.extract_body(), auth.get_gmail_service() 구현 완료"

provides:
  - "collect_samples.py: Gmail API로 출판사별 ASAP 메일 HTML을 tests/fixtures/에 저장하는 수집 스크립트"
  - "save_fixture(): 발신자별 메일 HTML fixture 저장 함수"
  - "verify_senders(): publishers.json sender vs 실제 Gmail From 헤더 대조 검증 함수"

affects:
  - 03-parser-impl (파서 구현에서 fixture 파일 사용)

tech-stack:
  added: []
  patterns:
    - "fixture 수집 스크립트: Gmail API → HTML 추출 → tests/fixtures/ 저장 패턴"
    - "sender 검증: From 헤더 파싱 (display name <email> 형식 처리)"

key-files:
  created:
    - collect_samples.py
  modified: []

key-decisions:
  - "collect_samples.py는 프로젝트 루트에 배치 (플랫 구조 유지, per D-02)"
  - "From 헤더에서 이메일 추출 시 '<email>' 앵글 브라켓 형식 처리 포함"
  - "불일치 발견 시 publishers.json 자동 수정 후 저장 (수동 수정 오류 방지)"

patterns-established:
  - "Gmail fixture 수집: save_fixture(key, sender) → tests/fixtures/{key}_{idx:02d}.html"
  - "발신자 검증: verify_senders() 불일치 → publishers.json 자동 수정"

requirements-completed: [PARSE-01, PARSE-02, PARSE-03]

duration: 2min
completed: 2026-04-04
---

# Phase 03 Plan 01: 샘플 수집 스크립트 및 publishers.json sender 검증 Summary

**Gmail API로 ACS/Elsevier/Science 출판사 메일 HTML을 수집하는 collect_samples.py 작성 완료 — save_fixture/verify_senders/main 3개 함수, publishers.json sender 자동 검증/수정 포함**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-04T15:40:19Z
- **Completed:** 2026-04-04T15:42:00Z
- **Tasks:** 1/2 (Task 2는 human-verify 체크포인트)
- **Files modified:** 1

## Accomplishments

- collect_samples.py 생성: Gmail API로 출판사별 메일 HTML fixture 수집 스크립트
- verify_senders(): publishers.json sender vs 실제 Gmail From 헤더 자동 대조
- save_fixture(): 메일 HTML을 tests/fixtures/{key}_{idx:02d}.html로 저장
- publishers.json 불일치 시 자동 수정 로직 포함
- import OK 검증 통과

## Task Commits

1. **Task 1: collect_samples.py 작성 및 publishers.json sender 검증** - `70990f9` (feat)
2. **Task 2: fixture 수집 실행** - 체크포인트 대기 중 (human-verify)

## Files Created/Modified

- `collect_samples.py` - Gmail API로 출판사별 ASAP 메일 HTML을 tests/fixtures/에 저장하는 수집 스크립트 (186줄)

## Decisions Made

- collect_samples.py는 프로젝트 루트에 배치 (플랫 구조 유지, per D-02)
- From 헤더에서 이메일 추출 시 `display name <email>` 형식 파싱 포함
- 발신자 불일치 시 publishers.json 자동 수정 후 저장 (수동 오류 방지)

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

None

## User Setup Required

**Task 2 실행 전 Gmail 인증 확인 필요:**

1. Gmail 인증 상태 확인:
   ```
   .venv\Scripts\python verify_gmail.py
   ```
   (라벨 목록이 출력되면 OK)

2. 수집 스크립트 실행:
   ```
   .venv\Scripts\python collect_samples.py
   ```

3. 확인 사항:
   - 각 출판사별 메일 검색 결과 (0건이면 sender 불일치 의심)
   - sender 불일치 시 자동 수정 메시지 출력 여부
   - tests/fixtures/ 디렉토리에 HTML 파일 생성 여부

4. fixture 검증:
   ```
   ls tests/fixtures/
   ```
   acs_01.html, elsevier_01.html, science_01.html 존재 확인

## Next Phase Readiness

- collect_samples.py 완료 → 사용자가 직접 실행하여 fixture 수집 필요
- fixture 파일 확보 후 Plan 02 (파서 구현) 진행 가능
- publishers.json sender 검증 완료 후 can_parse() 매칭 정확도 보장

---
*Phase: 03-parser-impl*
*Completed: 2026-04-04*
