# Phase 3: 출판사 파서 구현 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-04
**Phase:** 03-parser-impl
**Areas discussed:** 메일 샘플 확보 전략, HTML 파싱 방식, 파싱 실패 처리, 테스트 데이터 전략

---

## 메일 샘플 확보 전략

| Option | Description | Selected |
|--------|-------------|----------|
| 실제 메일 먼저 수집 | Gmail에서 출판사별 ASAP 메일을 1-2건씩 받은 후 HTML을 fixture로 저장하고 파서 개발 | ✓ |
| 출판사 웹사이트 HTML 기반 목업 | ACS/Elsevier/Science 출판사 웹사이트 HTML 구조를 참고하여 예상 메일 목업 작성 | |
| 최소한 목업 + 후속 보정 | DOI 패턴과 제목 태그 등 공통 패턴 기반으로 최소한 목업 작성, 실제 메일 수신 후 리팩토링 | |

**User's choice:** 실제 메일 먼저 수집
**Notes:** 3개 출판사 모두 Gmail에 ASAP 메일이 이미 존재함 확인

| Option | Description | Selected |
|--------|-------------|----------|
| Gmail에서 직접 받기 | 각 출판사 ASAP 알림을 구독 신청하고 수신된 메일을 수집 | |
| Gmail에 이미 있는 메일 사용 | Gmail API로 가져와서 HTML을 fixture로 저장하는 수집 스크립트 작성 | ✓ |
| 수동 저장 | Gmail 웹에서 메일 원본 보기(Show Original) 후 HTML 복사하여 파일로 저장 | |

**User's choice:** Gmail에 이미 있는 메일 사용

---

## HTML 파싱 방식

| Option | Description | Selected |
|--------|-------------|----------|
| BeautifulSoup4 위주 | BS4 CSS selector로 HTML 구조 탐색 + DOI는 정규식으로 추출 | ✓ |
| 정규식 위주 | HTML 태그 무시하고 정규식만으로 제목/DOI 추출 | |
| 혼합 (BS4 + 정규식) | BS4로 구조 탐색 후 정규식으로 정밀 추출 | |

**User's choice:** BeautifulSoup4 위주

| Option | Description | Selected |
|--------|-------------|----------|
| href 속성에서 추출 | doi.org 링크의 href 속성에서 DOI 추출 | ✓ |
| 전체 텍스트 정규식 스캔 | HTML 전체에서 10.\d{4,}/\S+ 패턴을 정규식으로 스캔 | |
| Claude가 판단 | 실제 메일 HTML을 본 후 최적의 방식 결정 | |

**User's choice:** href 속성에서 추출

| Option | Description | Selected |
|--------|-------------|----------|
| 출판사별 1파일 | parsers/acs.py, parsers/elsevier.py, parsers/science.py | ✓ |
| 출판사 그룹별 파일 | 유사한 패턴의 출판사를 그룹화 | |

**User's choice:** 출판사별 1파일

---

## 파싱 실패 처리

| Option | Description | Selected |
|--------|-------------|----------|
| 부분 추출 허용 | DOI 없이 제목만 있어도 PaperMetadata로 반환 | |
| DOI 필수 — 없으면 스킵 | DOI가 없는 논문은 건너뜀 | |
| Claude가 판단 | 실제 메일 파싱 결과를 보고 최적의 전략 결정 | ✓ |

**User's choice:** Claude가 판단 — 실제 메일 HTML 분석 후 결정

| Option | Description | Selected |
|--------|-------------|----------|
| logging.warning + 계속 | Python logging 모듈로 warning 로그 기록 후 다음 메일로 진행 | ✓ |
| print + 계속 | 단순히 print로 에러 출력 후 계속 | |
| Claude가 판단 | 구현 시 적절한 로깅 수준 결정 | |

**User's choice:** logging.warning + 계속

---

## 테스트 데이터 전략

| Option | Description | Selected |
|--------|-------------|----------|
| 실제 메일 HTML fixture | 수집 스크립트로 저장한 실제 메일 HTML을 tests/fixtures/에 보관 | ✓ |
| 최소한 목업 HTML | 필요한 부분만 담은 최소한 HTML 목업 작성 | |
| 둘 다 사용 | 실제 HTML fixture + 최소한 목업 모두 사용 | |

**User's choice:** 실제 메일 HTML fixture

| Option | Description | Selected |
|--------|-------------|----------|
| 포함 | 수집 스크립트(collect_samples.py)를 Plan 1으로 만들고, 수집 후 파서 개발을 Plan 2로 진행 | ✓ |
| 수동 수집 | 수집 스크립트 없이 사용자가 직접 Gmail에서 다운로드하여 fixture 디렉토리에 저장 | |
| Claude가 판단 | 계획 단계에서 적절한 접근 결정 | |

**User's choice:** 포함

---

## Claude's Discretion

- 부분 추출(DOI 누락) 처리 정책 — 실제 메일 분석 후 결정
- 출판사별 CSS selector 세부 구현
- collect_samples.py 세부 구현
- 논문 제목 추출 시 HTML 태그 정리 방식

## Deferred Ideas

None — discussion stayed within phase scope
