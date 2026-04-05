---
phase: 02-mail-detection
plan: "01"
subsystem: api
tags: [python, dataclass, abc, importlib, gmail, plugin-architecture]

requires:
  - phase: 01-auth-env-setup
    provides: config.py (GMAIL_SCOPES), auth.py, tests/conftest.py

provides:
  - PaperMetadata dataclass (title, doi, journal, date, authors, url)
  - publishers.json (ACS, Elsevier, Science 발신자-저널 매핑)
  - BaseParser ABC (can_parse, parse 추상 메서드 강제)
  - parser_registry.load_parsers() (parsers/ 자동 디스커버리)
  - config.py gmail.modify scope 확장

affects:
  - 02-mail-detection
  - 03-parsing-logic
  - 04-notion-integration

tech-stack:
  added: []
  patterns:
    - "Strategy Pattern: BaseParser ABC + 파일 추가만으로 새 파서 등록"
    - "TDD: 실패 테스트 먼저 작성 후 최소 구현으로 통과"
    - "importlib.util 자동 디스커버리: __subclasses__() 기반 플러그인 등록"
    - "inspect.isabstract 필터링: 추상 클래스 인스턴스화 방지"

key-files:
  created:
    - models.py
    - publishers.json
    - parsers/__init__.py
    - parsers/base.py
    - parser_registry.py
    - tests/test_models.py
    - tests/test_parser_registry.py
  modified:
    - config.py

key-decisions:
  - "inspect.isabstract로 BaseParser 미완성 서브클래스 인스턴스화 방지 (테스트 환경의 in-memory 클래스 오염 대응)"
  - "publishers.json 발신자 이메일은 플레이스홀더 -- Phase 3 시작 전 실제 메일에서 확인 후 수정 필요"
  - "load_parsers()는 parsers/ 기본값 사용, 테스트 시 tmp_path로 격리 가능"

patterns-established:
  - "BaseParser 상속 시 can_parse + parse 두 메서드 모두 필수 구현"
  - "파서 추가 방법: parsers/ 디렉토리에 BaseParser 상속 .py 파일 추가만으로 자동 등록"
  - "parser_registry.load_parsers()는 inspect.isabstract 필터로 안전하게 인스턴스화"

requirements-completed: [PARSE-04, PARSE-05]

duration: 3min
completed: "2026-04-03"
---

# Phase 02 Plan 01: 데이터 계약 및 파서 플러그인 구조 Summary

**PaperMetadata dataclass + publishers.json 출판사 매핑 + BaseParser ABC + importlib 자동 디스커버리로 파서 플러그인 프레임워크 구축**

## Performance

- **Duration:** 3 min
- **Started:** 2026-04-03T11:01:38Z
- **Completed:** 2026-04-03T11:04:33Z
- **Tasks:** 2
- **Files modified:** 8

## Accomplishments

- PaperMetadata dataclass 완성 (title, doi, journal, date + optional authors, url)
- publishers.json에 ACS, Elsevier, Science 3개 출판사 발신자-저널 매핑 구성
- config.py GMAIL_SCOPES를 gmail.modify로 확장 (라벨 부여를 위해)
- BaseParser ABC (can_parse + parse 추상 메서드 강제)
- parser_registry.load_parsers() — parsers/ 디렉토리 importlib 자동 스캔
- 11개 단위 테스트 전부 통과

## Task Commits

1. **Task 1: 데이터 모델 + 출판사 설정 + scope 확장** - `8cd3f6e` (feat)
2. **Task 2: BaseParser ABC + 자동 디스커버리 레지스트리** - `04abe92` (feat)

## Files Created/Modified

- `models.py` - PaperMetadata dataclass (title, doi, journal, date + optional authors, url)
- `publishers.json` - ACS, Elsevier, Science 발신자-이름-저널 매핑 (플레이스홀더, Phase 3에서 수정 필요)
- `config.py` - GMAIL_SCOPES gmail.modify로 확장 (per D-05)
- `parsers/__init__.py` - 패키지 초기화 (빈 파일)
- `parsers/base.py` - BaseParser ABC (can_parse, parse 추상 메서드)
- `parser_registry.py` - parsers/ 자동 디스커버리 + inspect.isabstract 필터
- `tests/test_models.py` - PaperMetadata + publishers.json 5개 테스트
- `tests/test_parser_registry.py` - BaseParser + load_parsers 6개 테스트

## Decisions Made

- `inspect.isabstract` 사용: `load_parsers()`가 `BaseParser.__subclasses__()`를 인스턴스화할 때 추상 메서드가 남은 불완전한 서브클래스(테스트에서 생성된 부분 구현 클래스 등)를 안전하게 필터링
- `publishers.json` 발신자 이메일은 플레이스홀더 — Phase 3 시작 전 실제 ASAP 메일 수신 후 정확한 발신자 주소 확인 및 수정 필요

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] inspect.isabstract 필터 추가로 in-memory 추상 서브클래스 인스턴스화 오류 방지**
- **Found during:** Task 2 (test_auto_discovery 실행 시)
- **Issue:** 테스트 2, 3에서 생성된 MissingCanParse, MissingParse 클래스가 `__subclasses__()`에 전역적으로 남아, `load_parsers()` 호출 시 해당 추상 클래스 인스턴스화로 TypeError 발생
- **Fix:** `load_parsers()`의 반환문에 `inspect.isabstract(cls)` 필터 추가 — 구체 구현이 완성된 클래스만 인스턴스화
- **Files modified:** parser_registry.py
- **Verification:** 6개 테스트 전부 PASSED
- **Committed in:** `04abe92` (Task 2 커밋에 포함)

---

**Total deviations:** 1 auto-fixed (Rule 1 - Bug)
**Impact on plan:** 정확성 보장을 위한 필수 수정. 스코프 변경 없음.

## Issues Encountered

- `__subclasses__()` 전역 상태 문제: Python 프로세스 내에서 정의된 모든 BaseParser 서브클래스가 누적되어 테스트 격리가 깨짐 → `inspect.isabstract` 필터로 해결. 향후 파서 추가 시에도 동일 방어 필요.

## Known Stubs

- `publishers.json` 발신자 이메일 (`alerts@acs.org`, `ealerts@elsevier.com`, `ScienceAdvances@sciencemag.org`): 플레이스홀더 값. Phase 3 파서 개발 전 실제 ASAP 메일에서 정확한 발신자 주소 확인 후 수정 필요. (계획된 미완성 — 02-RESEARCH.md NOTE 참조)

## User Setup Required

**gmail.modify scope 변경으로 재인증 필요:**
- 기존 `token.json` 삭제: `del token.json`
- `python auth.py` 실행하여 재인증 (브라우저 OAuth 흐름)
- 새 scope로 새 `token.json` 생성 확인

## Next Phase Readiness

- Plan 02 (gmail_client.py)가 의존하는 모든 데이터 계약 완성
- `PaperMetadata` 반환 타입으로 파서-클라이언트 인터페이스 확정
- `publishers.json`에서 발신자 이메일로 출판사 조회 로직 준비 완료
- 파서 플러그인 구조 준비 완료 — Phase 3에서 출판사별 파서 파일 추가만 하면 자동 등록

## Self-Check: PASSED

- FOUND: models.py
- FOUND: publishers.json
- FOUND: parsers/__init__.py
- FOUND: parsers/base.py
- FOUND: parser_registry.py
- FOUND: tests/test_models.py
- FOUND: tests/test_parser_registry.py
- FOUND commit: 8cd3f6e (Task 1)
- FOUND commit: 04abe92 (Task 2)
- `class PaperMetadata` 정의 수: 1
- `gmail.modify` in config.py: 확인
- publishers.json schema: OK
- `class BaseParser(ABC)` 정의 수: 1
- `def load_parsers` 정의 수: 1
- `abstractmethod` 사용 수: 3 (데코레이터 import + can_parse + parse)
- `__subclasses__` 사용 수: 2 (registry + inspect 필터)

---
*Phase: 02-mail-detection*
*Completed: 2026-04-03*
