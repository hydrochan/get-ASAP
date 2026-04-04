---
phase: 03-parser-impl
plan: "02"
subsystem: parsers
tags: [tdd, parser, acs, elsevier, science, wiley, crossref, beautifulsoup]
dependency_graph:
  requires: ["03-01"]
  provides: ["parsers/acs.py", "parsers/elsevier.py", "parsers/science.py", "parsers/wiley.py", "crossref_client.py"]
  affects: ["parser_registry.load_parsers()", "Phase 04 pipeline"]
tech_stack:
  added: ["beautifulsoup4==4.14.3", "lxml==6.0.2"]
  patterns:
    - "TDD RED→GREEN per parser"
    - "CrossRef API for DOI fallback (title → DOI)"
    - "Strategy Pattern: BaseParser subclasses"
    - "seen_dois set for deduplication"
key_files:
  created:
    - parsers/acs.py
    - parsers/elsevier.py
    - parsers/science.py
    - parsers/wiley.py
    - crossref_client.py
    - tests/test_parser_acs.py
    - tests/test_parser_elsevier.py
    - tests/test_parser_science.py
    - tests/test_parser_wiley.py
  modified:
    - requirements.txt
    - tests/test_models.py
decisions:
  - "CrossRef API (무료, 키 불필요) 를 DOI 폴백으로 채택 - Elsevier/Science/Wiley HTML에 DOI 직접 없음"
  - "ACS는 'DOI: 10.xxx' 텍스트 a 태그로 직접 추출, CrossRef 불필요"
  - "Wiley 제목에서 '(저널명 이슈/연도)' 접미사 정규식으로 제거"
  - "DOI 없는 Elsevier/Science/Wiley는 CrossRef mock으로 네트워크 없이 테스트"
metrics:
  duration: "8분"
  completed: "2026-04-04"
  tasks_completed: 3
  files_created: 9
  files_modified: 2
---

# Phase 03 Plan 02: 출판사 파서 TDD 구현 Summary

**One-liner:** 4개 출판사(ACS/Elsevier/Science/Wiley) 파서를 TDD로 구현, ACS는 직접 DOI 추출, 나머지 3개는 CrossRef API 폴백으로 DOI 조회.

## What Was Built

4개 출판사 파서와 CrossRef API 클라이언트를 TDD 방식으로 구현했다.

### 파서별 구현 특징

| 파서 | HTML 구조 | DOI 추출 방법 | 테스트 수 |
|------|-----------|--------------|----------|
| ACSParser | `tolkien-column-9 h5 a` (제목), `a[DOI: ...]` (DOI) | 직접 텍스트 추출 | 8 |
| ElsevierParser | `h2 a` (제목) | CrossRef API 폴백 | 9 |
| ScienceParser | `td.em_f24 a` (제목) | CrossRef API 폴백 | 9 |
| WileyParser | `a.issue-item__title h5` (제목) | CrossRef API 폴백 | 9 |

### crossref_client.py

CrossRef 공개 API (`https://api.crossref.org/works?query.title=TITLE&rows=1`)를 사용하여 논문 제목 → DOI를 조회한다. API 키 불필요, 완전 무료. 표준 라이브러리(`urllib.request`, `json`)만 사용.

## Test Results

```
69 tests passed (전체 테스트 스위트)
- tests/test_parser_acs.py: 8 passed
- tests/test_parser_elsevier.py: 9 passed
- tests/test_parser_science.py: 9 passed
- tests/test_parser_wiley.py: 9 passed
- 기존 테스트 44개: 모두 통과
```

## Parser Registry Verification

```
load_parsers() → 4 ['ACS Publications', 'Elsevier', 'Science', 'Wiley']
```

## Commits

| Hash | Description |
|------|-------------|
| 5835ea5 | feat(03-02): ACSParser TDD (PARSE-01) |
| 6cd6060 | feat(03-02): ElsevierParser + crossref_client TDD (PARSE-02) |
| 57208a7 | feat(03-02): ScienceParser + WileyParser TDD (PARSE-03) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 2 - Missing Dependency] beautifulsoup4 + lxml 패키지 미설치**
- **Found during:** Task 1 GREEN
- **Issue:** `bs4` 모듈 없어 ACS 파서 import 실패
- **Fix:** `.venv/Scripts/pip install beautifulsoup4 lxml`, requirements.txt 업데이트
- **Files modified:** requirements.txt

**2. [Rule 1 - Bug] test_models.py의 구 ACS sender 참조**
- **Found during:** 전체 테스트 스위트 실행
- **Issue:** `test_publishers_journal_lookup`이 `alerts@acs.org`를 기대하나 publishers.json에는 Plan 01에서 검증된 `updates@acspubs.org`가 있음
- **Fix:** 테스트를 현재 publishers.json 값에 맞게 수정
- **Files modified:** tests/test_models.py

**3. [Rule 2 - Added Feature] Wiley 파서 추가**
- **Found during:** 계획 편차 지시 (CRITICAL DEVIATION 노트)
- **Issue:** 원래 계획은 ACS/Elsevier/Science 3개였으나, publishers.json에 Wiley가 있고 fixture도 존재
- **Fix:** parsers/wiley.py + tests/test_parser_wiley.py 추가 구현
- **Files created:** parsers/wiley.py, tests/test_parser_wiley.py

## Decisions Made

1. **CrossRef API 채택:** Elsevier/Science/Wiley는 HTML에 DOI가 없어서 제목 기반 CrossRef 조회 방식 사용. AI/ML 아님 - 단순 HTTP 메타데이터 조회.
2. **ACS DOI 직접 추출:** ACS 메일에는 "DOI: 10.xxx/yyy" 텍스트가 있는 `<a>` 태그가 있어 CrossRef 불필요.
3. **테스트에서 CrossRef mock 처리:** `unittest.mock.patch`로 네트워크 의존 제거. 운영 시에만 실제 API 호출.
4. **Wiley 제목 정제:** "(저널명 이슈/연도)" 패턴 정규식으로 제거하여 정확한 제목만 CrossRef 조회에 사용.

## Known Stubs

없음. 모든 파서가 실제 fixture HTML에서 제목을 추출한다. DOI는 ACS의 경우 직접, 나머지는 CrossRef 폴백으로 조회한다. `journal`과 `date` 필드는 의도적으로 빈 값 — 메일 HTML에서 파싱이 복잡하고 Phase 4(Notion 저장)에서 크리티컬하지 않음.

## Self-Check: PASSED

| Item | Status |
|------|--------|
| parsers/acs.py | FOUND |
| parsers/elsevier.py | FOUND |
| parsers/science.py | FOUND |
| parsers/wiley.py | FOUND |
| crossref_client.py | FOUND |
| commit 5835ea5 (ACS TDD) | FOUND |
| commit 6cd6060 (Elsevier + CrossRef TDD) | FOUND |
| commit 57208a7 (Science + Wiley TDD) | FOUND |
