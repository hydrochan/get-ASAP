# Phase 3: 출판사 파서 구현 - Research

**Researched:** 2026-04-04
**Domain:** HTML 이메일 파싱 (BeautifulSoup4 + CSS selector + 정규식 DOI 추출)
**Confidence:** HIGH

## Summary

Phase 3는 두 개의 독립 작업으로 구성된다. Plan 1: `collect_samples.py`로 실제 Gmail ASAP 메일 HTML을 `tests/fixtures/`에 저장한다. Plan 2: 저장된 fixture를 기반으로 TDD 방식으로 `parsers/acs.py`, `parsers/elsevier.py`, `parsers/science.py` 세 파서를 구현한다.

기술 스택은 이미 확정되어 있다. `beautifulsoup4 4.14.3`(lxml 백엔드 포함)이 venv에 설치되어 있고, CSS attribute substring selector(`a[href*="doi.org"]`)와 DOI 정규식(`10\.\d{4,9}/[^\s"<>#?&]+`)의 조합이 동작함을 로컬에서 검증했다. `BaseParser` ABC, `PaperMetadata` dataclass, `parser_registry.py` 자동 디스커버리가 Phase 2에서 완성되어 있다.

부분 추출(제목 있고 DOI 누락) 처리는 Claude 재량(D-07)이며, Phase 4 중복 방지가 DOI 기반이므로 DOI 없는 논문은 `logging.warning` 후 스킵이 안전하다. 실제 메일 HTML 분석 전까지 CSS selector 세부 구현은 확정하기 어려우므로 Plan 1(샘플 수집) 완료 후 Plan 2(파서 구현)가 반드시 순서대로 진행되어야 한다.

**Primary recommendation:** collect_samples.py → fixture 확보 → 실제 HTML 구조 분석 → CSS selector 확정 → TDD 파서 구현 순으로 진행한다.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** 실제 Gmail에 이미 수신된 ASAP 메일을 사용하여 파서 개발 (3개 출판사 모두 메일 존재 확인됨)
- **D-02:** Gmail API로 출판사별 메일을 가져와 HTML을 tests/fixtures/에 저장하는 수집 스크립트(collect_samples.py) 작성
- **D-03:** 수집 스크립트를 Plan 1으로, 파서 구현을 Plan 2로 순차 진행
- **D-04:** BeautifulSoup4 CSS selector 위주로 HTML 구조 탐색 (lxml 백엔드 사용)
- **D-05:** DOI는 href 속성에서 추출 — doi.org 링크의 href에서 DOI 패턴 추출
- **D-06:** 출판사별 1파일 — parsers/acs.py, parsers/elsevier.py, parsers/science.py
- **D-08:** 파서 예외/전체 파싱 실패 시 logging.warning으로 기록 후 다음 메일로 계속 진행 (실패한 메일 ID와 에러 내용 포함)
- **D-09:** 실제 메일 HTML을 tests/fixtures/에 보관하여 테스트 fixture로 사용
- **D-10:** TDD 패턴 유지 (pytest + mock)

### Claude's Discretion
- 부분 추출(DOI 누락) 시 저장 vs 스킵 정책 — 실제 메일 HTML 분석 후 결정
- 출판사별 CSS selector 세부 구현
- collect_samples.py의 세부 구현 (저장 형식, 파일명 규칙 등)
- 논문 제목 추출 시 HTML 태그 정리 방식

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| PARSE-01 | ACS 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | parsers/acs.py, BS4 CSS selector + DOI regex, fixture: tests/fixtures/acs_01.html |
| PARSE-02 | Elsevier 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | parsers/elsevier.py, BS4 CSS selector + DOI regex, fixture: tests/fixtures/elsevier_01.html |
| PARSE-03 | Science 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다 | parsers/science.py, BS4 CSS selector + DOI regex, fixture: tests/fixtures/science_01.html |
</phase_requirements>

---

## Project Constraints (from CLAUDE.md)

- **No AI:** AI/ML 없이 정규식 기반 파싱만 사용 — BeautifulSoup4 + re 모듈만 허용
- **Tech Stack:** Python 기반, beautifulsoup4 + lxml 사용 (이미 venv에 설치됨)
- **Auth:** 외부 API 키 하드코딩 금지 — collect_samples.py도 auth.py의 `get_gmail_service()` 재사용
- **TDD:** pytest + mock 패턴 유지 (Phase 1-2에서 확립됨)
- **에러 처리:** 에러 발생 시 즉시 중단 금지 — logging.warning 후 계속 진행 (D-08과 일치)

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| beautifulsoup4 | 4.14.3 (설치됨) | HTML 파싱, CSS selector | D-04 결정, lxml 백엔드 지원 |
| lxml | 6.0.2 (설치됨) | BS4 백엔드 파서 | D-04 결정, 속도와 CSS4 selector 완전 지원 |
| re (stdlib) | - | DOI 패턴 추출 | D-05 결정, 외부 의존 없음 |
| logging (stdlib) | - | 파싱 실패 기록 | D-08 결정 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| auth.py (기존) | - | Gmail 인증 재사용 | collect_samples.py에서 get_gmail_service() 호출 |
| publishers.json (기존) | - | 출판사 sender 정보 | can_parse() 구현에서 이메일 주소 매칭 |
| pytest | 8.3.5 (설치됨) | 단위 테스트 | TDD RED/GREEN/REFACTOR 사이클 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| lxml 백엔드 | html.parser (stdlib) | html.parser는 CSS4 selector 일부 미지원, 속도 느림 — lxml 사용 유지 |
| CSS selector | XPath | XPath는 HTML 이메일에서 취약, CSS selector가 더 직관적 |
| re DOI regex | 직접 URL 슬라이싱 | regex가 다양한 DOI 형식 처리에 안전 |

**Installation:** 이미 설치됨 — 추가 설치 불필요
```bash
# 확인만: beautifulsoup4==4.14.3, lxml==6.0.2 venv에 존재
.venv/Scripts/python -c "import bs4; import lxml; print('OK')"
```

---

## Architecture Patterns

### Recommended Project Structure
```
get-ASAP/
├── parsers/
│   ├── __init__.py      # 기존
│   ├── base.py          # 기존 (BaseParser ABC)
│   ├── acs.py           # Plan 2에서 신규 생성
│   ├── elsevier.py      # Plan 2에서 신규 생성
│   └── science.py       # Plan 2에서 신규 생성
├── tests/
│   ├── fixtures/        # Plan 1에서 신규 생성
│   │   ├── acs_01.html       # ACS 메일 HTML fixture
│   │   ├── elsevier_01.html  # Elsevier 메일 HTML fixture
│   │   └── science_01.html   # Science 메일 HTML fixture
│   ├── test_parser_acs.py      # Plan 2에서 신규 생성
│   ├── test_parser_elsevier.py # Plan 2에서 신규 생성
│   └── test_parser_science.py  # Plan 2에서 신규 생성
└── collect_samples.py   # Plan 1에서 신규 생성
```

### Pattern 1: 출판사 파서 BaseParser 서브클래스 구조
**What:** BaseParser를 상속하여 can_parse + parse 두 메서드를 구현한다
**When to use:** 모든 출판사 파서에 적용
**Example:**
```python
# parsers/acs.py — ACS 파서 골격
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')


class ACSParser(BaseParser):
    publisher_name = "ACS Publications"

    def can_parse(self, sender: str, subject: str) -> bool:
        # publishers.json의 acs.sender 값과 매칭
        return sender == "alerts@acs.org"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            # CSS selector로 DOI 포함 링크 탐색 (실제 선택자는 fixture 분석 후 확정)
            for link in soup.select('a[href*="doi.org"]'):
                doi = _extract_doi(link.get("href", ""))
                if not doi:
                    continue
                title = link.get_text(separator=" ", strip=True)
                if not title:
                    continue
                papers.append(PaperMetadata(
                    title=title,
                    doi=doi,
                    journal="",   # infer_journal()으로 채움
                    date="",
                ))
            return papers
        except Exception as e:
            logger.warning("ACS 파싱 실패: %s", e)
            return []


def _extract_doi(href: str) -> str:
    """href에서 DOI 패턴 추출"""
    m = DOI_RE.search(href)
    return m.group() if m else ""
```

### Pattern 2: collect_samples.py — Gmail API로 fixture 저장
**What:** 출판사별 최신 메일 HTML을 tests/fixtures/에 저장하는 일회성 스크립트
**When to use:** Plan 1 전용 (이후 삭제하지 않고 유지 — 새 출판사 추가 시 재사용)
**Example:**
```python
# collect_samples.py
import json, os
from auth import get_gmail_service
from gmail_client import extract_body

def collect(publisher_key: str, sender: str, count: int = 2):
    """출판사 sender로 메일 검색 후 HTML fixture 저장"""
    service = get_gmail_service()
    response = service.users().messages().list(
        userId="me",
        q=f"from:{sender}",
        maxResults=count
    ).execute()
    messages = response.get("messages", [])
    
    os.makedirs("tests/fixtures", exist_ok=True)
    for idx, msg in enumerate(messages, start=1):
        full = service.users().messages().get(
            userId="me", id=msg["id"], format="full"
        ).execute()
        html_body = extract_body(full["payload"])
        path = f"tests/fixtures/{publisher_key}_{idx:02d}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html_body)
        print(f"저장: {path} ({len(html_body)} bytes)")
```

### Pattern 3: TDD fixture 기반 테스트
**What:** 저장된 fixture HTML을 읽어 파서 동작을 검증하는 단위 테스트
**When to use:** Plan 2의 모든 파서 테스트
**Example:**
```python
# tests/test_parser_acs.py
import os, pytest
from parsers.acs import ACSParser

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "acs_01.html")

@pytest.fixture
def acs_html():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()

def test_acs_can_parse():
    parser = ACSParser()
    assert parser.can_parse("alerts@acs.org", "JACS ASAP") is True
    assert parser.can_parse("other@domain.com", "JACS ASAP") is False

def test_acs_parse_returns_papers(acs_html):
    parser = ACSParser()
    papers = parser.parse(acs_html)
    assert len(papers) > 0
    for paper in papers:
        assert paper.doi.startswith("10.")
        assert paper.title != ""
```

### Anti-Patterns to Avoid
- **fixture 없이 파서 먼저 구현:** CSS selector는 실제 HTML 구조를 봐야 확정할 수 있다. Plan 1 완료 전에 선택자를 가정하고 파서를 작성하면 반드시 수정이 필요해진다.
- **단일 CSS selector만 사용:** 출판사 메일 포맷은 버전마다 다를 수 있다. 2-3개 폴백 selector를 순서대로 시도하는 방어적 구현이 필요하다.
- **html.parser 백엔드 사용:** CSS attribute substring selector(`[href*="..."]`)는 html.parser에서 동작하지만 CSS4 selector 완전 지원을 위해 lxml을 사용한다.
- **DOI 후처리 생략:** href에서 추출한 DOI 끝에 `.`이나 `,` 같은 불필요한 문자가 붙을 수 있다. `rstrip(".,;")` 같은 정리가 필요하다.
- **예외 없이 None 반환:** `parse()`는 실패 시 `[]`를 반환해야 한다. `None` 반환은 호출자에서 TypeError를 유발한다.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| HTML 파싱 | 정규식으로 HTML 직접 파싱 | BeautifulSoup4 + lxml | HTML은 중첩 구조, 정규식은 edge case에 취약 |
| DOI URL 슬라이싱 | href.split("doi.org/")[1] | `re.compile(r'10\.\d{4,9}/...')` | 다양한 DOI URL 형식(dx.doi.org, pubs.acs.org/doi/ 등) 처리 |
| Gmail 인증 | 새 OAuth 흐름 작성 | `auth.get_gmail_service()` | 이미 Phase 1에서 완성됨 |
| 본문 디코딩 | base64 직접 처리 | `gmail_client.extract_body()` | 이미 Phase 2에서 완성됨 |

**Key insight:** 이 phase의 핵심 작업은 "HTML 구조 분석 + CSS selector 선택"이다. 인프라(인증, 디코딩, 레지스트리)는 모두 완성되어 있으므로 중복 구현하지 않는다.

---

## Common Pitfalls

### Pitfall 1: publishers.json sender 이메일 플레이스홀더
**What goes wrong:** STATE.md에 명시됨 — "publishers.json 발신자 이메일은 플레이스홀더 -- Phase 3 시작 전 실제 메일에서 확인 후 수정 필요"
**Why it happens:** Phase 2에서 실제 메일 없이 플레이스홀더로 설정됨
**How to avoid:** collect_samples.py 실행 후 실제 메일 발신자 주소를 확인하고 publishers.json 업데이트
**Warning signs:** can_parse()가 항상 False 반환 — 발신자 주소 불일치 의심

### Pitfall 2: ACS 메일의 중복 DOI 링크
**What goes wrong:** ACS ASAP 메일은 논문 하나에 DOI 링크가 여러 개 포함될 수 있다 (TOC 이미지 링크, 제목 링크, Full text 링크)
**Why it happens:** HTML 이메일은 같은 URL을 여러 곳에 반복 사용
**How to avoid:** DOI 중복 제거 로직 추가 (`seen_dois: set` 활용)
**Warning signs:** `parse()` 결과에 같은 DOI가 여러 번 등장

### Pitfall 3: HTML 이메일 인코딩 문제
**What goes wrong:** 논문 제목에 특수문자(화학식, 그리스 문자 등)가 HTML entity(`&alpha;`, `&rarr;`)로 인코딩되어 있음
**Why it happens:** 학술 논문 제목에는 특수 기호가 자주 사용됨
**How to avoid:** `get_text()`는 HTML entity를 자동 디코딩하므로 별도 처리 불필요. 단, `separator=" "`를 사용해 sub/sup 태그 주변 공백 유지
**Warning signs:** 제목에 `&` 또는 `;` 문자가 남아있으면 entity 처리 누락

### Pitfall 4: collect_samples.py — Gmail 인증이 필요한 실행
**What goes wrong:** collect_samples.py는 브라우저 OAuth 흐름을 요구할 수 있음 (token.json 만료 시)
**Why it happens:** token.json은 만료되면 자동 갱신되지만, refresh_token 없으면 브라우저 재인증 필요
**How to avoid:** Plan 1 실행 전 `verify_gmail.py`로 인증 상태 확인
**Warning signs:** `get_gmail_service()` 호출 시 브라우저가 열리거나 InstalledAppFlow 실행됨

### Pitfall 5: 빈 fixture 상태에서 TDD RED 작성
**What goes wrong:** tests/fixtures/가 없거나 비어있으면 fixture 기반 테스트가 FileNotFoundError로 실패
**Why it happens:** Plan 1이 완료되지 않은 상태에서 Plan 2 테스트 작성
**How to avoid:** Plan 2 테스트는 `pytest.mark.skipif(not os.path.exists(FIXTURE), reason="fixture 없음")`으로 보호하거나, Plan 1이 반드시 선행되어야 함을 명시
**Warning signs:** test 실행 시 FileNotFoundError 또는 OSError

---

## Code Examples

### DOI 추출 핵심 패턴 (검증됨)
```python
# Source: 로컬 검증 (Python re, 2026-04-04)
import re

DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')

def extract_doi_from_href(href: str) -> str:
    """href 속성에서 DOI 추출. 없으면 빈 문자열 반환."""
    match = DOI_RE.search(href)
    if not match:
        return ""
    # 끝에 붙은 구두점 제거 (HTML 파싱 아티팩트)
    return match.group().rstrip(".,;)")
```

### BeautifulSoup4 CSS selector DOI 링크 탐색 (검증됨)
```python
# Source: 로컬 검증 (beautifulsoup4 4.14.3 + lxml 6.0.2, 2026-04-04)
from bs4 import BeautifulSoup

def find_doi_links(html: str) -> list:
    """HTML에서 DOI 포함 a 태그 목록 반환. 폴백 selector 순서대로 시도."""
    soup = BeautifulSoup(html, "lxml")
    
    # 폴백 selector 순서: doi.org 링크 → 출판사별 DOI 경로
    selectors = [
        'a[href*="doi.org"]',
        'a[href*="/doi/10."]',
    ]
    
    for selector in selectors:
        links = soup.select(selector)
        if links:
            return links
    return []
```

### 논문 제목 텍스트 추출 (검증됨)
```python
# Source: 로컬 검증 (beautifulsoup4 4.14.3, 2026-04-04)
def clean_title(tag) -> str:
    """BS4 Tag에서 제목 텍스트 추출 및 정리.
    
    get_text(separator=" ") 사용 이유:
    - sub/sup 태그 주변 공백 자동 삽입 (화학식: CO2 → CO 2 → 후처리로 교정)
    - HTML entity 자동 디코딩 (&alpha; → α)
    """
    text = tag.get_text(separator=" ", strip=True)
    # 연속 공백 단일화
    import re
    return re.sub(r'\s+', ' ', text).strip()
```

### collect_samples.py 핵심 패턴
```python
# collect_samples.py 기본 구조
import os, json
from auth import get_gmail_service
from gmail_client import extract_body

def save_fixture(publisher_key: str, sender: str, max_count: int = 2) -> list[str]:
    """출판사 발신자로 최근 메일 가져와 fixtures/에 저장. 저장된 파일 경로 목록 반환."""
    service = get_gmail_service()
    response = service.users().messages().list(
        userId="me",
        q=f"from:{sender}",
        maxResults=max_count
    ).execute()
    
    saved = []
    os.makedirs("tests/fixtures", exist_ok=True)
    for idx, msg_meta in enumerate(response.get("messages", []), start=1):
        full_msg = service.users().messages().get(
            userId="me", id=msg_meta["id"], format="full"
        ).execute()
        html = extract_body(full_msg["payload"])
        path = f"tests/fixtures/{publisher_key}_{idx:02d}.html"
        with open(path, "w", encoding="utf-8") as f:
            f.write(html)
        saved.append(path)
        print(f"저장: {path} ({len(html):,} bytes)")
    return saved
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| html.parser 기본 백엔드 | lxml 백엔드 | BS4 4.x | CSS4 selector 완전 지원, 속도 향상 |
| BeautifulSoup `findAll()` | `.select()` CSS selector | BS4 4.7+ | SoupSieve 통합으로 CSS4 selector 지원 |
| `a[href^="https://doi.org"]` (exact prefix) | `a[href*="doi.org"]` (substring) | - | dx.doi.org, pubs.acs.org/doi/ 등 다양한 형식 포괄 |

**Deprecated/outdated:**
- `BeautifulSoup(html, 'html.parser')` for CSS selector: 기능은 동작하지만 CSS4 일부 미지원 — lxml 사용 권장
- `findAll()`: `find_all()`의 구 별칭, 동작하지만 신규 코드에서는 `find_all()` 또는 `select()` 사용

---

## Open Questions

1. **실제 ACS/Elsevier/Science 메일 HTML 구조**
   - What we know: DOI는 `a[href*="doi.org"]` 또는 `a[href*="/doi/"]` 패턴으로 추출 가능. 제목은 해당 링크 텍스트 또는 근처 헤딩 태그
   - What's unclear: 각 출판사별 정확한 CSS class, 중첩 테이블 구조, 논문 1건당 몇 개의 DOI 링크가 있는지
   - Recommendation: Plan 1(collect_samples.py) 실행 후 fixture HTML을 브라우저에서 열어 DevTools로 구조 확인 후 CSS selector 확정

2. **publishers.json sender 이메일 정확성**
   - What we know: STATE.md에 "플레이스홀더" 경고 있음. 현재 값: alerts@acs.org, ealerts@elsevier.com, ScienceAdvances@sciencemag.org
   - What's unclear: 실제 수신 메일의 From 헤더 정확한 형식 (대소문자, 서브도메인 여부)
   - Recommendation: Plan 1 실행 시 발신자 주소를 출력하고 publishers.json과 비교 — 불일치 시 즉시 수정

3. **부분 추출(DOI 누락) 정책**
   - What we know: D-07은 Claude 재량. Phase 4 중복 방지가 DOI 기반
   - What's unclear: 실제 메일에서 DOI 누락이 얼마나 빈번한지
   - Recommendation: DOI 없으면 `logging.warning` 후 스킵(None 반환 아닌 빈 리스트에 미포함). 제목만 있는 경우는 Phase 4에서 저장 가치 없음

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| beautifulsoup4 | HTML 파싱 (D-04) | ✓ | 4.14.3 | — |
| lxml | BS4 백엔드 (D-04) | ✓ | 6.0.2 | — |
| pytest | TDD (D-10) | ✓ | 8.3.5 | — |
| Google Gmail API | collect_samples.py | ✓ (auth.py) | google-api-python-client 2.193.0 | — |
| token.json (Gmail 인증) | collect_samples.py 실행 | ✓ (존재 확인) | — | verify_gmail.py로 갱신 |

**Missing dependencies with no fallback:** 없음

**Missing dependencies with fallback:** 없음 — 모든 의존성 사용 가능

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 |
| Config file | 없음 (pytest 자동 탐색) |
| Quick run command | `.venv/Scripts/pytest tests/test_parser_acs.py tests/test_parser_elsevier.py tests/test_parser_science.py -x` |
| Full suite command | `.venv/Scripts/pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| PARSE-01 | ACS 파서가 fixture HTML에서 DOI와 제목 추출 | unit (fixture 기반) | `.venv/Scripts/pytest tests/test_parser_acs.py -x` | ❌ Wave 0 |
| PARSE-02 | Elsevier 파서가 fixture HTML에서 DOI와 제목 추출 | unit (fixture 기반) | `.venv/Scripts/pytest tests/test_parser_elsevier.py -x` | ❌ Wave 0 |
| PARSE-03 | Science 파서가 fixture HTML에서 DOI와 제목 추출 | unit (fixture 기반) | `.venv/Scripts/pytest tests/test_parser_science.py -x` | ❌ Wave 0 |
| PARSE-01~03 | can_parse()가 올바른 sender에만 True 반환 | unit (mock 불필요) | `.venv/Scripts/pytest tests/ -k "can_parse" -x` | ❌ Wave 0 |
| PARSE-01~03 | 파싱 실패 시 빈 리스트 반환 (예외 없음) | unit | `.venv/Scripts/pytest tests/ -k "parse_failure" -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `.venv/Scripts/pytest tests/test_parser_{publisher}.py -x`
- **Per wave merge:** `.venv/Scripts/pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_parser_acs.py` — PARSE-01: ACS 파서 단위 테스트
- [ ] `tests/test_parser_elsevier.py` — PARSE-02: Elsevier 파서 단위 테스트
- [ ] `tests/test_parser_science.py` — PARSE-03: Science 파서 단위 테스트
- [ ] `tests/fixtures/` 디렉토리 — Plan 1(collect_samples.py) 완료 후 생성됨
- [ ] `tests/fixtures/acs_01.html` — Plan 1 산출물, Plan 2 테스트 선행 조건
- [ ] `tests/fixtures/elsevier_01.html` — Plan 1 산출물
- [ ] `tests/fixtures/science_01.html` — Plan 1 산출물

> 주의: Plan 2 테스트(test_parser_*.py)는 fixture 없으면 FileNotFoundError. Plan 1 완료 후 작성 또는 `pytest.mark.skipif(not os.path.exists(FIXTURE), reason="fixture 미수집")` 가드 적용 권장

---

## Sources

### Primary (HIGH confidence)
- 로컬 코드 분석: `parsers/base.py`, `models.py`, `parser_registry.py`, `publishers.json`, `gmail_client.py` — 인터페이스 및 통합 포인트 확인
- 로컬 검증: BS4 4.14.3 + lxml 6.0.2 CSS selector + DOI regex 동작 확인 (2026-04-04)
- STATE.md / CONTEXT.md — 플레이스홀더 경고 및 확정 결정 사항

### Secondary (MEDIUM confidence)
- [Beautiful Soup Documentation 4.14.3](https://www.crummy.com/software/BeautifulSoup/bs4/doc/) — CSS selector (soupsieve), get_text() 동작
- [Mastering CSS Selectors in BeautifulSoup](https://scrapingant.com/blog/beautifulsoup-css-selectors) — attribute selector 패턴

### Tertiary (LOW confidence)
- ACS ASAP 메일 HTML 구조 세부 사항 — 웹에서 찾기 어려움, **실제 fixture 분석 후 확정 필요**
- Elsevier/Science 메일 HTML 구조 세부 사항 — 동일

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — 설치된 버전 로컬 직접 확인
- Architecture: HIGH — BaseParser/PaperMetadata 기존 코드 완전 분석, 통합 포인트 명확
- Pitfalls: MEDIUM — 일반적 HTML 이메일 파싱 패턴 기반, 출판사별 세부 구조는 LOW
- CSS selector 세부 구현: LOW — 실제 fixture HTML 분석 전까지 확정 불가

**Research date:** 2026-04-04
**Valid until:** 2026-05-04 (30일 — beautifulsoup4/lxml은 안정적)
