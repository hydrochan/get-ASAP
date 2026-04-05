---
phase: 05-deploy
plan: 01
subsystem: infra
tags: [python, argparse, logging, RotatingFileHandler, pipeline, orchestrator, gmail, notion, crossref]

# Dependency graph
requires:
  - phase: 01-auth-env-setup
    provides: get_gmail_service, config.py, Gmail OAuth 인증
  - phase: 02-mail-detection
    provides: gmail_client.py (build_query, get_new_messages, extract_body, mark_processed, get_or_create_label, infer_journal, load_state, save_state)
  - phase: 03-parser-impl
    provides: parser_registry.py (load_parsers), crossref_client.py (lookup_doi)
  - phase: 04-notion
    provides: notion_client_mod.py (get_or_create_db, save_papers)

provides:
  - main.py: 전체 파이프라인 오케스트레이터 (Gmail -> 파싱 -> CrossRef -> Notion -> 라벨)
  - run_pipeline(dry_run): 파이프라인 실행 함수
  - setup_logging(verbose): RotatingFileHandler + stdout 로깅 설정
  - parse_args(): --dry-run, --verbose argparse CLI
  - logs/get-asap.log: 실행 로그 파일

affects: [cron 스케줄링, 배포 가이드, 운영 모니터링]

# Tech tracking
tech-stack:
  added: [logging.handlers.RotatingFileHandler, argparse]
  patterns:
    - try/except per-message error isolation (메일별 에러 격리)
    - dry_run 분기로 실제 API 호출 방지
    - 파이프라인 조기 종료 패턴 (메일 0건 시)

key-files:
  created:
    - main.py
    - tests/test_main.py
    - logs/ (디렉토리)
  modified: []

key-decisions:
  - "main.py는 모든 외부 모듈을 import해 단일 진입점으로 전체 파이프라인 조합"
  - "dry_run=True 시 get_or_create_label 호출 자체를 스킵하여 불필요한 API 요청 방지"
  - "개별 메일 에러는 try/except로 감싸 스킵 후 계속 진행 (파이프라인 중단 방지)"
  - "mock_paper에서 doi/journal을 빈 문자열로 설정해야 lookup_doi/infer_journal 호출이 테스트에서 트리거됨"

patterns-established:
  - "파이프라인 오케스트레이터 패턴: 각 모듈의 public 함수만 import하여 순차 연결"
  - "TDD: 실패 테스트 먼저 커밋(test:), 구현 후 통과(feat:) 커밋"

requirements-completed: [DEPLOY-01, DEPLOY-02]

# Metrics
duration: 4min
completed: 2026-04-04
---

# Phase 5 Plan 01: main.py 파이프라인 오케스트레이터 구현 Summary

**argparse CLI(--dry-run/--verbose)와 RotatingFileHandler 로깅을 갖춘 Gmail->파싱->CrossRef->Notion->라벨 마킹 전체 파이프라인 오케스트레이터**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-04T17:37:55Z
- **Completed:** 2026-04-04T17:41:48Z
- **Tasks:** 1
- **Files modified:** 2

## Accomplishments

- Gmail 메일 수신부터 Notion 저장까지 전체 파이프라인을 main.py 단일 파일로 통합
- --dry-run 모드에서 save_papers/mark_processed 완전 격리 (불필요한 API 호출 없음)
- RotatingFileHandler(5MB, backup 3개)로 logs/get-asap.log에 영속 로그 기록
- 개별 메일 에러 시 try/except 격리로 파이프라인 중단 없이 계속 진행
- 7개 단위 테스트 전부 통과 (TDD RED-GREEN 완료)

## Task Commits

각 태스크를 원자적으로 커밋:

1. **TDD RED - test(05-01): add failing tests** - `b266b63` (test)
2. **TDD GREEN - feat(05-01): implement main.py pipeline orchestrator** - `fd3c3ff` (feat)

## Files Created/Modified

- `main.py` - 전체 파이프라인 오케스트레이터 (run_pipeline, setup_logging, parse_args, 헬퍼 함수)
- `tests/test_main.py` - 7개 단위 테스트 (dry_run, 파이프라인 순서, 에러 스킵, argparse, 조기 종료, 헬퍼)
- `logs/` - 로그 디렉토리 생성 (런타임에 자동 생성됨)

## Decisions Made

- **dry_run 시 get_or_create_label 호출 자체 스킵:** dry_run=True이면 label_id가 필요 없으므로 API 호출을 아예 하지 않음 (플랜의 "dry_run 아닐 때만" 조건 충실히 구현)
- **mock_paper 픽스처 doi/journal 빈 문자열:** 테스트 3에서 lookup_doi 호출 검증을 위해 doi="" 설정 필요 - 파이프라인에서 doi가 비어있을 때만 lookup_doi를 호출하는 로직 때문

## Deviations from Plan

None - 플랜에 명시된 모든 요구사항을 정확히 구현. 테스트 픽스처 데이터 조정은 로직 변경이 아닌 테스트 데이터 수정임.

## Issues Encountered

- **Test 3 초기 실패:** mock_paper에 doi="10.1021/test.doi"가 있어 lookup_doi가 호출되지 않음. doi=""로 수정하여 해결.

## User Setup Required

None - main.py 실행은 기존 .env + token.json 설정으로 충분.

## Known Stubs

None - 모든 외부 API 호출은 실제 모듈로 연결됨. dry_run 모드의 콘솔 출력은 의도된 동작.

## Next Phase Readiness

- main.py가 완성되어 `python main.py` 단일 명령으로 전체 파이프라인 실행 가능
- cron 설정: `0 */6 * * * cd /home/ubuntu/get-ASAP && /home/ubuntu/get-ASAP/.venv/bin/python main.py >> logs/cron.log 2>&1`
- 오라클 클라우드 배포 절차 (05-02-PLAN)로 진행 가능

## Self-Check: PASSED

- main.py: FOUND
- tests/test_main.py: FOUND
- 05-01-SUMMARY.md: FOUND
- Commit b266b63 (TDD RED): FOUND
- Commit fd3c3ff (TDD GREEN): FOUND

---
*Phase: 05-deploy*
*Completed: 2026-04-04*
