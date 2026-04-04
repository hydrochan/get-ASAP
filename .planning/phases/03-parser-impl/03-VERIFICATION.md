---
phase: 03-parser-impl
verified: 2026-04-04T09:00:00Z
status: passed
score: 5/5 must-haves verified
re_verification: false
---

# Phase 3: 출판사 파서 구현 Verification Report

**Phase Goal:** ACS, Elsevier, Science 출판사 ASAP 메일에서 논문 제목과 DOI가 정확히 추출된다
**Verified:** 2026-04-04
**Status:** passed
**Re-verification:** No — initial verification

> **Note:** Phase was expanded beyond the original plan. Wiley parser was added (4 publishers total: ACS, Elsevier, Science, Wiley). DOI extraction for Elsevier/Science/Wiley uses CrossRef API fallback (`crossref_client.lookup_doi()`) instead of direct href extraction, because those publishers do not expose DOIs in their HTML. This is an intentional scope expansion, not a deviation that blocks the goal.

---

## Goal Achievement

### Observable Truths (from ROADMAP.md Success Criteria)

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | ACS ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다 | VERIFIED | `parsers/acs.py` ACSParser 구현. 실제 fixture `acs_01.html` (983줄)에서 테스트 통과. `test_acs_parse_extracts_papers`, `test_acs_parse_doi_format` 모두 PASSED |
| 2 | Elsevier ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다 | VERIFIED | `parsers/elsevier.py` ElsevierParser 구현. 실제 fixture `elsevier_01.html` (554줄)에서 테스트 통과. CrossRef mock으로 네트워크 없이 DOI 조회 검증. 9개 테스트 PASSED |
| 3 | Science ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다 | VERIFIED | `parsers/science.py` ScienceParser 구현. 실제 fixture `science_01.html` (3801줄)에서 테스트 통과. CrossRef mock 사용. 9개 테스트 PASSED |
| 4 | 파싱에 실패한 메일은 건너뛰고 로그에 기록되며 전체 파이프라인은 계속 실행된다 | VERIFIED | 모든 파서에 `try/except Exception as e: logger.warning(...); return []` 패턴 적용. `test_*_parse_failure_returns_empty` 및 `test_*_parse_empty_returns_empty` 테스트 PASSED |
| 5 | parser_registry.load_parsers()가 파서를 자동 발견한다 | VERIFIED | `load_parsers()` 실행 결과: `4 ['ACS Publications', 'Elsevier', 'Science', 'Wiley']` |

**Score:** 5/5 truths verified

---

## Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `parsers/acs.py` | ACS 파서 (BaseParser 상속) | VERIFIED | `class ACSParser(BaseParser)` 존재. 111줄. BeautifulSoup + lxml 백엔드. DOI 직접 추출 ("DOI: 10.xxx" 텍스트 링크). |
| `parsers/elsevier.py` | Elsevier 파서 (BaseParser 상속) | VERIFIED | `class ElsevierParser(BaseParser)` 존재. 93줄. CrossRef API 폴백으로 DOI 조회. |
| `parsers/science.py` | Science 파서 (BaseParser 상속) | VERIFIED | `class ScienceParser(BaseParser)` 존재. 98줄. CrossRef API 폴백으로 DOI 조회. |
| `parsers/wiley.py` | Wiley 파서 (BaseParser 상속) — 계획 확장 | VERIFIED | `class WileyParser(BaseParser)` 존재. 103줄. CrossRef API 폴백. 제목 후처리 정규식 포함. |
| `crossref_client.py` | CrossRef API 조회 유틸리티 — 계획 확장 | VERIFIED | `lookup_doi(title)` 함수 구현. 표준 라이브러리만 사용(`urllib.request`, `json`). 69줄. |
| `tests/test_parser_acs.py` | ACS 파서 단위 테스트 | VERIFIED | 8개 test_ 함수. 73줄. 모두 PASSED. |
| `tests/test_parser_elsevier.py` | Elsevier 파서 단위 테스트 | VERIFIED | 9개 test_ 함수. 97줄. CrossRef mock(`unittest.mock.patch`) 사용. 모두 PASSED. |
| `tests/test_parser_science.py` | Science 파서 단위 테스트 | VERIFIED | 9개 test_ 함수. 91줄. CrossRef mock 사용. 모두 PASSED. |
| `tests/test_parser_wiley.py` | Wiley 파서 단위 테스트 — 계획 확장 | VERIFIED | 9개 test_ 함수. 90줄. CrossRef mock 사용. 모두 PASSED. |
| `tests/fixtures/acs_01.html` | ACS 실제 메일 HTML fixture | VERIFIED | 983줄. 실제 Gmail에서 수집. |
| `tests/fixtures/elsevier_01.html` | Elsevier 실제 메일 HTML fixture | VERIFIED | 554줄. 실제 Gmail에서 수집. |
| `tests/fixtures/science_01.html` | Science 실제 메일 HTML fixture | VERIFIED | 3801줄. 실제 Gmail에서 수집. |
| `tests/fixtures/wiley_01.html` | Wiley 실제 메일 HTML fixture — 계획 확장 | VERIFIED | 2477줄. 실제 Gmail에서 수집. |
| `collect_samples.py` | Gmail fixture 수집 스크립트 | VERIFIED | `save_fixture`, `verify_senders`, `main` 3개 함수 구현. `from auth import get_gmail_service`, `from gmail_client import extract_body` 연결. |

---

## Key Link Verification

| From | To | Via | Status | Details |
|------|----|-----|--------|---------|
| `parsers/acs.py` | `parsers/base.py` | `class ACSParser(BaseParser)` | WIRED | Line 14: `class ACSParser(BaseParser):` |
| `parsers/elsevier.py` | `parsers/base.py` | `class ElsevierParser(BaseParser)` | WIRED | Line 15: `class ElsevierParser(BaseParser):` |
| `parsers/science.py` | `parsers/base.py` | `class ScienceParser(BaseParser)` | WIRED | Line 19: `class ScienceParser(BaseParser):` |
| `parsers/wiley.py` | `parsers/base.py` | `class WileyParser(BaseParser)` | WIRED | Line 19: `class WileyParser(BaseParser):` |
| `parsers/acs.py` | `models.py` | `from models import PaperMetadata` | WIRED | Line 5: `from models import PaperMetadata`. PaperMetadata 인스턴스 생성 확인. |
| `parsers/elsevier.py` | `crossref_client.py` | `crossref_client.lookup_doi(title)` | WIRED | Line 5: `import crossref_client`. Line 67: `doi = crossref_client.lookup_doi(title)` |
| `parsers/science.py` | `crossref_client.py` | `crossref_client.lookup_doi(title)` | WIRED | Line 5: `import crossref_client`. Line 72: `doi = crossref_client.lookup_doi(title)` |
| `parsers/wiley.py` | `crossref_client.py` | `crossref_client.lookup_doi(title)` | WIRED | Line 5: `import crossref_client`. Line 83: `doi = crossref_client.lookup_doi(title)` |
| `parser_registry.load_parsers()` | `parsers/*.py` | importlib auto-discovery | WIRED | `load_parsers()` → `4 ['ACS Publications', 'Elsevier', 'Science', 'Wiley']` 확인 |
| `collect_samples.py` | `auth.get_gmail_service()` | `from auth import get_gmail_service` | WIRED | Line 10: `from auth import get_gmail_service` |
| `collect_samples.py` | `gmail_client.extract_body()` | `from gmail_client import extract_body` | WIRED | Line 11: `from gmail_client import extract_body` |
| `collect_samples.py` | `publishers.json` | `json.load` | WIRED | Line 139: `publishers_path = "publishers.json"` |

---

## Data-Flow Trace (Level 4)

| Artifact | Data Variable | Source | Produces Real Data | Status |
|----------|---------------|--------|--------------------|--------|
| `parsers/acs.py` | `papers` list | `soup.select("table.tolkien-column-9")` → `h5 a` 텍스트 + `"DOI:" a` 태그 | 실제 fixture HTML에서 추출. `test_acs_parse_extracts_papers` PASSED. | FLOWING |
| `parsers/elsevier.py` | `papers` list | `soup.select("h2 a")` 텍스트 + `crossref_client.lookup_doi()` | 제목은 실제 fixture에서 추출. DOI는 CrossRef API (테스트에서 mock으로 검증). | FLOWING |
| `parsers/science.py` | `papers` list | `soup.select("td.em_f24 a")` 텍스트 + `crossref_client.lookup_doi()` | 제목은 실제 fixture에서 추출. DOI는 CrossRef API (테스트에서 mock으로 검증). | FLOWING |
| `parsers/wiley.py` | `papers` list | `soup.select("a.issue-item__title")` → `h5` 텍스트 + `crossref_client.lookup_doi()` | 제목은 실제 fixture에서 추출. DOI는 CrossRef API (테스트에서 mock으로 검증). | FLOWING |

**참고:** `journal`과 `date` 필드는 의도적으로 빈값. SUMMARY.md에서 "메일 HTML에서 파싱이 복잡하고 Phase 4(Notion 저장)에서 크리티컬하지 않음"으로 명시. Phase 4에서 다루어야 할 항목이지만 현재 Phase 3의 목표(제목과 DOI 추출)에는 영향 없음.

---

## Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| 전체 테스트 스위트 69개 통과 | `.venv/Scripts/pytest tests/ -v` | `69 passed in 2.89s` | PASS |
| parser_registry가 4개 파서 자동 발견 | `python -c "from parser_registry import load_parsers; ps=load_parsers(); print(len(ps), [p.publisher_name for p in ps])"` | `4 ['ACS Publications', 'Elsevier', 'Science', 'Wiley']` | PASS |
| collect_samples.py import 가능 | `python -c "import collect_samples"` | (import OK — from auth, gmail_client 연결 포함) | PASS |

---

## Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|-------------|-------------|--------|----------|
| PARSE-01 | 03-01-PLAN.md, 03-02-PLAN.md | ACS 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | SATISFIED | `parsers/acs.py` ACSParser. 실제 `acs_01.html` fixture (983줄). 8개 테스트 PASSED. DOI 직접 추출 (`"DOI: 10.xxx"` 텍스트 파싱). |
| PARSE-02 | 03-01-PLAN.md, 03-02-PLAN.md | Elsevier 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | SATISFIED | `parsers/elsevier.py` ElsevierParser. 실제 `elsevier_01.html` fixture (554줄). 9개 테스트 PASSED. CrossRef API 폴백으로 DOI 조회. |
| PARSE-03 | 03-01-PLAN.md, 03-02-PLAN.md | Science 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | SATISFIED | `parsers/science.py` ScienceParser. 실제 `science_01.html` fixture (3801줄). 9개 테스트 PASSED. CrossRef API 폴백으로 DOI 조회. |

**REQUIREMENTS.md 추적성 확인:**
- PARSE-01, PARSE-02, PARSE-03 모두 `[x]` (완료) 표시
- Traceability 테이블에서 Phase 3 → Complete 상태 확인

**추가 발견 — 계획 확장으로 인한 REQUIREMENTS.md v2 항목 조기 달성:**
- `Wiley 출판사 파서 추가` (v2 Deferred)가 이미 Phase 3에서 구현됨 (`parsers/wiley.py`, `tests/test_parser_wiley.py`, `tests/fixtures/wiley_01.html`). v2 항목이므로 Phase 3 검증 통과에는 영향 없음. REQUIREMENTS.md v2 섹션 업데이트 여부는 팀 판단 사항.

**고아 요구사항 없음:** REQUIREMENTS.md에서 Phase 3에 매핑된 추가 ID 없음.

---

## Anti-Patterns Found

| File | Pattern | Severity | Impact |
|------|---------|----------|--------|
| 없음 | — | — | — |

**검토 결과:**
- `return []` 패턴: 모든 파서에서 빈 입력 guard (`if not message_body or not message_body.strip(): return []`) 및 exception handler (`except Exception: return []`)로만 사용. 정상적인 방어 코드이며 스텁 아님. 대응 테스트가 의도적으로 이 동작을 검증.
- 모든 파서 파일에 TODO/FIXME/PLACEHOLDER 없음.
- `journal=""`, `date=""` 필드: SUMMARY.md에서 의도적 빈값으로 명시. Phase 4에서 필요하지 않은 필드. 스텁 아님.

---

## Human Verification Required

### 1. CrossRef API 실제 DOI 정확성

**Test:** 운영 환경에서 Elsevier/Science/Wiley 파서로 실제 메일 파싱 시 CrossRef가 반환하는 DOI가 실제 논문의 DOI와 일치하는지 확인
**Expected:** 파싱된 제목으로 CrossRef 검색 시 정확한 DOI 반환 (제목이 정확할수록 정확도 높음)
**Why human:** 네트워크 의존 동작. 테스트에서 mock 처리되어 실제 CrossRef 정확도는 자동 검증 불가. 제목 철자/약어 차이로 오조회 가능성 있음.

### 2. publishers.json sender 이메일 변경 대응

**Test:** Gmail에서 실제 메일 수신 시 `can_parse()` sender 매칭이 정상 동작하는지 확인
**Expected:** `updates@acspubs.org`, `sciencedirect@notification.elsevier.com`, `announcements@aaas.sciencepubs.org`, `WileyOnlineLibrary@wiley.com` 발신자 메일이 각 파서에 올바르게 라우팅됨
**Why human:** sender 이메일 불일치는 코드가 아니라 publishers.json 실제 수신 확인으로만 검증 가능. Plan 01에서 collect_samples.py 실행으로 검증 완료되었다고 SUMMARY에 기록되어 있으나, 추후 발신자 주소 변경 가능성 존재.

---

## Gaps Summary

없음. 모든 자동화 검증 항목 통과.

---

## Summary

Phase 3 목표 "ACS, Elsevier, Science 출판사 ASAP 메일에서 논문 제목과 DOI가 정확히 추출된다"가 완전히 달성되었다.

**달성된 핵심 사항:**

1. **4개 출판사 파서 구현** (계획 3개 → 실제 4개): ACS, Elsevier, Science, Wiley 모두 BaseParser 상속, can_parse/parse 인터페이스 구현
2. **DOI 추출 전략 분화:** ACS는 HTML에서 직접 추출, Elsevier/Science/Wiley는 CrossRef API 폴백 (출판사별 HTML 구조 차이 대응)
3. **69개 테스트 전체 통과** (기존 Phase 2 테스트 포함, 회귀 없음)
4. **실제 fixture HTML 기반 테스트:** 모든 파서가 실제 Gmail 수신 메일 HTML로 검증
5. **PARSE-01, PARSE-02, PARSE-03 요구사항 충족** (REQUIREMENTS.md 완료 표시)
6. **parser_registry.load_parsers()가 4개 파서 자동 발견** 확인

---

_Verified: 2026-04-04_
_Verifier: Claude (gsd-verifier)_
