---
phase: 02-mail-detection
plan: "02"
subsystem: api
tags: [python, gmail-api, historyId, incremental-sync, base64url, tdd, mock]

requires:
  - phase: 02-mail-detection-01
    provides: publishers.json (발신자-저널 매핑), models.py (PaperMetadata), config.py (gmail.modify scope)
  - phase: 01-auth-env-setup
    provides: auth.py (get_gmail_service), tests/conftest.py

provides:
  - gmail_client.py (8개 함수: 메일 필터링, 증분 동기화, 라벨 부여, 본문 디코딩, 저널명 추론)
  - tests/test_gmail_client.py (14개 단위 테스트)
  - state.json 영속화 패턴 (historyId 기반 증분 동기화)

affects:
  - 02-mail-detection (token.json gmail.modify 재인증 필요)
  - 03-parsing-logic (extract_body + infer_journal 인터페이스 사용)

tech-stack:
  added: []
  patterns:
    - "historyId 증분 동기화: messages.list(초기) → history.list(증분) → 404 폴백"
    - "base64url 디코딩: data + '==' 패딩 추가 후 urlsafe_b64decode"
    - "multipart 재귀 파싱: text/html 우선, text/plain 폴백"
    - "TDD: 실패 테스트 먼저 커밋(RED), 구현 후 통과 커밋(GREEN)"

key-files:
  created:
    - gmail_client.py
    - tests/test_gmail_client.py
  modified: []

key-decisions:
  - "historyId 404 폴백: state['historyId'] = None 후 재귀 호출로 전체 동기화 트리거"
  - "초기 동기화: messages.list의 첫 메시지 historyId를 기준점으로 저장 (다음 실행부터 증분 동기화)"
  - "base64url 패딩: Gmail API는 패딩 없이 반환 → '==' 추가로 파이썬 디코딩 호환"

patterns-established:
  - "gmail_client.get_new_messages(): state dict를 인플레이스 갱신 (historyId 자동 업데이트)"
  - "save_state(): lastRunAt을 UTC ISO 형식으로 자동 갱신"
  - "get_or_create_label(): 라벨 캐싱 없이 매번 labels.list 호출 (단순성 우선)"

requirements-completed: [MAIL-01, MAIL-02, MAIL-03, PARSE-05]

duration: 10min
completed: "2026-04-03"
---

# Phase 02 Plan 02: Gmail 클라이언트 모듈 구현 Summary

**historyId 증분 동기화 + 404 폴백 + base64url 디코딩 + 저널명 추론을 갖춘 Gmail 클라이언트 모듈 구현 (TDD, 14개 테스트 통과)**

## Performance

- **Duration:** 10 min
- **Started:** 2026-04-03T11:07:34Z
- **Completed:** 2026-04-03T11:17:00Z
- **Tasks:** 1 완료 + 1 체크포인트 대기
- **Files modified:** 2

## Accomplishments

- gmail_client.py에 8개 함수 구현 (build_query, load_state, save_state, get_new_messages, get_or_create_label, mark_processed, extract_body, infer_journal)
- historyId 기반 증분 동기화 구현: 초기 동기화(messages.list) → 이후 증분(history.list) → 404 자동 폴백
- base64url 디코딩 및 multipart 재귀 파싱 구현 (text/html 우선, text/plain 폴백)
- TDD 방식: 14개 실패 테스트 먼저 커밋(RED) → 구현 후 전부 통과(GREEN)
- 전체 테스트 스위트 34개 전부 통과 (기존 20개 + 신규 14개)

## Task Commits

TDD 방식으로 2개 커밋:

1. **Task 1 RED: 실패 테스트 작성** - `7f27cb0` (test)
2. **Task 1 GREEN: gmail_client.py 구현** - `80eab24` (feat)

**Plan metadata:** (체크포인트 후 최종 커밋 예정)

_TDD 태스크: test → feat 2개 커밋_

## Files Created/Modified

- `gmail_client.py` - 8개 함수: 메일 필터링, historyId 증분 동기화, 라벨 부여, base64url 디코딩, 저널명 추론
- `tests/test_gmail_client.py` - 14개 단위 테스트 (Gmail API 전체 mock, tmp_path state.json 격리)

## Decisions Made

- **historyId 초기 기준점 확보 방법:** `messages.list` 결과의 첫 번째 메시지 `messages.get`으로 historyId를 추출하여 저장. 이후 실행부터 `history.list(startHistoryId=N)` 사용.
- **404 폴백 구현:** `state["historyId"] = None` 후 `get_new_messages()` 재귀 호출로 초기 동기화 트리거. 재귀 깊이는 1로 제한됨 (폴백 후 historyId가 None이므로 초기 동기화 경로 진입).
- **base64url 패딩:** Gmail API는 base64url을 패딩(`=`) 없이 반환. `data + "=="` 추가로 `urlsafe_b64decode` 호환.

## Deviations from Plan

None - 플랜 대로 정확히 구현됨.

## Issues Encountered

None.

## User Setup Required

**Task 2 (체크포인트): gmail.modify scope 재인증 필요**

Plan 01에서 config.py의 GMAIL_SCOPES를 `gmail.modify`로 변경했고, 이 플랜에서 `mark_processed`(라벨 부여) 기능을 구현했다. 기존 `token.json`은 `gmail.readonly` scope로 발급되었으므로 삭제 후 재인증이 필요하다.

재인증 단계:
1. 프로젝트 루트에서 기존 token.json 삭제: `del token.json` (Windows)
2. verify_gmail.py 실행: `.venv\Scripts\python.exe verify_gmail.py`
3. 브라우저에서 Google 계정 인증 + gmail.modify scope 동의
4. 콘솔에 Gmail 라벨 목록이 출력되면 성공
5. 전체 테스트: `.venv\Scripts\python.exe -m pytest tests/ -v`

## Next Phase Readiness

- `gmail_client.py` 완성 — Phase 3 파서 로직에서 `extract_body` + `infer_journal` 인터페이스 바로 사용 가능
- `get_new_messages` + `mark_processed` 조합으로 메인 실행 루프 구성 준비 완료
- token.json 재인증 후 실제 Gmail API 연결 테스트 필요 (체크포인트 Task 2)

## Known Stubs

- `publishers.json` 발신자 이메일 (플레이스홀더): Phase 3 시작 전 실제 ASAP 메일 수신 후 수정 필요 (02-01-SUMMARY.md에서 인계받은 Known Stub)

## Self-Check: PASSED

- FOUND: gmail_client.py
- FOUND: tests/test_gmail_client.py
- FOUND commit: 7f27cb0 (TDD RED)
- FOUND commit: 80eab24 (TDD GREEN)
- `def build_query` in gmail_client.py: 확인
- `def load_state` in gmail_client.py: 확인
- `def save_state` in gmail_client.py: 확인
- `def get_new_messages` in gmail_client.py: 확인
- `def get_or_create_label` in gmail_client.py: 확인
- `def mark_processed` in gmail_client.py: 확인
- `def extract_body` in gmail_client.py: 확인
- `def infer_journal` in gmail_client.py: 확인
- `history().list` 사용: 확인 (증분 동기화)
- `get-ASAP-processed` 라벨명: 확인
- `urlsafe_b64decode` 사용: 확인
- 14개 테스트 PASSED: 확인
- 전체 34개 테스트 PASSED: 확인

---
*Phase: 02-mail-detection*
*Completed: 2026-04-03*
