# Phase 3: 출판사 파서 구현 - Context

**Gathered:** 2026-04-04
**Status:** Ready for planning

<domain>
## Phase Boundary

ACS, Elsevier, Science 3개 출판사의 ASAP 알림 메일에서 논문 제목과 DOI를 추출하는 구체 파서를 구현한다. BaseParser를 상속한 출판사별 파서 파일을 parsers/ 디렉토리에 추가하여 자동 디스커버리로 등록한다.

</domain>

<decisions>
## Implementation Decisions

### 메일 샘플 확보 전략
- **D-01:** 실제 Gmail에 이미 수신된 ASAP 메일을 사용하여 파서 개발 (3개 출판사 모두 메일 존재 확인됨)
- **D-02:** Gmail API로 출판사별 메일을 가져와 HTML을 tests/fixtures/에 저장하는 수집 스크립트(collect_samples.py) 작성
- **D-03:** 수집 스크립트를 Plan 1으로, 파서 구현을 Plan 2로 순차 진행

### HTML 파싱 방식
- **D-04:** BeautifulSoup4 CSS selector 위주로 HTML 구조 탐색 (CLAUDE.md 기술 스택과 일치, lxml 백엔드 사용)
- **D-05:** DOI는 href 속성에서 추출 — doi.org 링크의 href에서 DOI 패턴(10.xxxx/yyyy) 추출
- **D-06:** 출판사별 1파일 — parsers/acs.py, parsers/elsevier.py, parsers/science.py 각각 BaseParser 서브클래스 구현

### 파싱 실패 처리
- **D-07:** 부분 추출(제목만 있고 DOI 누락) 처리는 Claude 재량으로 결정 — 실제 메일 파싱 결과를 보고 최적의 전략 결정
- **D-08:** 파서 예외/전체 파싱 실패 시 logging.warning으로 기록 후 다음 메일로 계속 진행 (실패한 메일 ID와 에러 내용 포함)

### 테스트 데이터 전략
- **D-09:** 실제 메일 HTML을 tests/fixtures/에 보관하여 테스트 fixture로 사용
- **D-10:** TDD 패턴 유지 (Phase 1-2에서 확립된 pytest + mock 패턴 계속 사용)

### Claude's Discretion
- 부분 추출(DOI 누락) 시 저장 vs 스킵 정책 — 실제 메일 HTML 분석 후 결정
- 출판사별 CSS selector 세부 구현
- collect_samples.py의 세부 구현 (저장 형식, 파일명 규칙 등)
- 논문 제목 추출 시 HTML 태그 정리 방식

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project-level
- `.planning/PROJECT.md` — 프로젝트 비전, 제약사항 (정규식 기반 파싱, AI 금지)
- `.planning/REQUIREMENTS.md` — PARSE-01, PARSE-02, PARSE-03 요구사항 정의

### Prior Phases
- `.planning/phases/02-mail-detection/02-CONTEXT.md` — BaseParser/PaperMetadata/parser_registry 설계 결정 (D-07, D-08)

### Existing Code (필수 참고)
- `parsers/base.py` — BaseParser ABC: can_parse(sender, subject), parse(message_body) 인터페이스
- `models.py` — PaperMetadata dataclass: title, doi, journal, date, authors(optional), url(optional)
- `parser_registry.py` — load_parsers() 자동 디스커버리 로직
- `publishers.json` — 3개 출판사 sender, name, journals 매핑
- `gmail_client.py` — extract_body() 본문 디코딩, infer_journal() 저널명 추론

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `BaseParser` (parsers/base.py): can_parse + parse 인터페이스 — 구체 파서가 상속하여 구현
- `PaperMetadata` (models.py): title, doi, journal, date + optional authors, url — parse() 반환 타입
- `parser_registry.py`: load_parsers() — parsers/ 디렉토리의 .py 파일 자동 로드 및 인스턴스화
- `publishers.json`: acs(alerts@acs.org), elsevier(ealerts@elsevier.com), science(ScienceAdvances@sciencemag.org) 매핑
- `gmail_client.py`의 extract_body(): multipart/base64url 디코딩 — 메일 본문 HTML 추출
- `gmail_client.py`의 infer_journal(): 저널명 추론 — 파서에서 journal 필드 채울 때 참고 가능

### Established Patterns
- TDD (pytest + mock 기반 단위 테스트) — Phase 1-2에서 확립
- 플랫 프로젝트 구조 — parsers/ 하위에 출판사별 파일 추가
- python-dotenv 기반 환경변수 관리
- publishers.json 외부 설정 파일 패턴

### Integration Points
- parsers/acs.py, elsevier.py, science.py → parser_registry.py의 load_parsers()로 자동 등록
- 각 파서의 can_parse() → publishers.json의 sender 정보와 매칭
- 각 파서의 parse() → gmail_client.py의 extract_body() 결과를 입력으로 받음
- 파서 반환값 PaperMetadata → Phase 4에서 Notion DB 저장에 사용

</code_context>

<specifics>
## Specific Ideas

- 수집 스크립트가 Gmail API를 사용하므로 auth.py의 get_gmail_service()를 재사용
- 출판사별 메일 1-2건을 fixture로 저장하면 충분 (다양한 케이스는 v2에서 추가)
- DOI 링크는 보통 `<a href="https://doi.org/10.xxxx/yyyy">` 형태로 포함됨

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 03-parser-impl*
*Context gathered: 2026-04-04*
