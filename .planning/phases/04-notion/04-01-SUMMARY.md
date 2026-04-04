---
phase: 04-notion
plan: 01
subsystem: database
tags: [notion, notion-client, python, tdd, crud, deduplication]

# Dependency graph
requires:
  - phase: 01-auth-env-setup
    provides: notion_auth.get_notion_client() 재사용
  - phase: 02-mail-detection
    provides: models.PaperMetadata 데이터 모델
provides:
  - notion_client_mod.py — Notion DB CRUD 기능 (create_paper_db, get_or_create_db, save_paper, save_papers)
  - notion-client 3.0.0 API 패턴 확립 (data_sources.query, initial_data_source)
affects: [05-integration, pipeline]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "notion-client 3.0.0: data_sources.query(data_source_id) — databases.query 미사용"
    - "DB 생성 시 initial_data_source.properties 아래 스키마 배치"
    - "_get_data_source_id discovery 패턴: databases.retrieve → data_sources[0].id"
    - "_call_with_retry: rate_limited 에러 시 1초 후 1회 재시도, 그 외 에러 logging.warning + 스킵"
    - "save_papers 배치 처리: data_source_id 1회 획득 후 캐싱"

key-files:
  created:
    - notion_client_mod.py
    - tests/test_notion_client.py
  modified:
    - config.py
    - .env.example

key-decisions:
  - "파일명 notion_client_mod.py 사용 — SDK 패키지 notion_client와 이름 충돌 방지 (런타임 ImportError 방지)"
  - "save_papers에서 data_source_id를 1회만 획득해 캐싱 — 배치 저장 시 API 호출 낭비 방지"
  - "NOTION_PARENT_PAGE_ID 환경변수 추가 — DB 자동 생성 경로 지원"

patterns-established:
  - "Pattern: notion-client 3.0.0 query flow: databases.retrieve → data_source_id → data_sources.query"
  - "Pattern: rate limit 재시도: _call_with_retry(fn, *args, **kwargs) — 1초 sleep 후 1회"
  - "Pattern: 중복 방지: DOI 있으면 rich_text equals, 없으면 title contains 필터"

requirements-completed: [NOTION-01, NOTION-02, NOTION-03]

# Metrics
duration: 4min
completed: 2026-04-04
---

# Phase 4 Plan 01: notion_client_mod 구현 Summary

**notion-client 3.0.0의 data_sources.query 패턴으로 DOI/제목 기반 중복 방지 + Notion DB CRUD 8개 함수 TDD 구현**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-04T16:53:07Z
- **Completed:** 2026-04-04T16:57:20Z
- **Tasks:** 2 (TDD RED + GREEN)
- **Files modified:** 4

## Accomplishments

- notion_client_mod.py: 8개 함수 구현 (create_paper_db, _get_data_source_id, get_or_create_db, _build_properties, _is_duplicate, _call_with_retry, save_paper, save_papers)
- notion-client 3.0.0 API 패턴 준수: data_sources.query 사용, databases.query 미사용
- 17개 단위 테스트 모두 통과 (TDD GREEN), 전체 86개 테스트 회귀 없음

## Task Commits

각 태스크를 원자적으로 커밋:

1. **Task 1: TDD RED — notion_client 테스트 작성** - `18fbddc` (test)
2. **Task 2: TDD GREEN — notion_client_mod.py 구현** - `c45c238` (feat)

## Files Created/Modified

- `notion_client_mod.py` — Notion DB CRUD 8개 함수 (create_paper_db, save_paper, save_papers 등)
- `tests/test_notion_client.py` — 17개 단위 테스트 (중복 방지, rate limit, 배치 저장 포함)
- `config.py` — NOTION_PARENT_PAGE_ID 환경변수 추가
- `.env.example` — NOTION_PARENT_PAGE_ID 항목 추가

## Decisions Made

- **파일명 notion_client_mod.py 사용**: SDK 패키지 `notion_client`와 이름 충돌 방지. `notion_client.py`로 명명 시 `from notion_client import Client`가 자기 자신을 순환 import하는 런타임 오류 발생
- **data_source_id 캐싱**: save_papers에서 databases.retrieve를 1회만 호출해 data_source_id를 획득. 배치 처리 시 논문 수만큼 중복 API 호출하는 낭비 방지
- **NOTION_PARENT_PAGE_ID 환경변수 추가**: NOTION_DATABASE_ID가 없을 때 DB 자동 생성을 위한 부모 페이지 ID가 필요. config.py와 .env.example에 추가

## Deviations from Plan

None - 플랜에 명시된 대로 정확히 실행됨.

## Issues Encountered

없음.

## Known Stubs

없음 — 모든 함수가 실제 API 호출 경로로 구현됨. 실제 Notion API 연결은 `.env`의 `NOTION_TOKEN`과 `NOTION_DATABASE_ID`(또는 `NOTION_PARENT_PAGE_ID`) 설정 후 사용 가능.

## Next Phase Readiness

- notion_client_mod.py 완성: Phase 4 Plan 02 (통합 파이프라인)에서 `from notion_client_mod import save_papers` 로 즉시 사용 가능
- get_or_create_db(): 파이프라인 진입점에서 DB ID 획득에 사용
- 모든 함수가 mock 기반으로 테스트됨: 실제 Notion API 없이도 테스트 통과

## Self-Check: PASSED

- notion_client_mod.py: FOUND
- tests/test_notion_client.py: FOUND
- 04-01-SUMMARY.md: FOUND
- Commit 18fbddc (TDD RED): FOUND
- Commit c45c238 (TDD GREEN): FOUND

---
*Phase: 04-notion*
*Completed: 2026-04-04*
