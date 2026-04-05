# Phase 2: 메일 감지 프레임워크 - Research

**Researched:** 2026-04-03
**Domain:** Gmail API (history.list, messages, labels) + Python plugin architecture (ABC + importlib)
**Confidence:** HIGH

## Summary

Phase 2는 Gmail API를 통한 ASAP 메일 필터링, historyId 기반 증분 동기화, 처리 완료 라벨 부여, 그리고 파서 플러그인 구조 구축으로 구성된다. 모든 핵심 기술(Gmail API v1, Python ABC, importlib 자동 디스커버리)은 안정적이고 문서가 충분하다.

증분 동기화의 핵심은 `history.list()` API이다. 최초 실행 시 `messages.list()`로 최신 메일의 historyId를 추출하여 state.json에 저장하고, 이후 실행에서 이 값을 `startHistoryId`로 사용한다. 404 응답 시 전체 동기화로 폴백하는 처리가 필수다.

파서 플러그인 구조는 Python의 `abc.ABC` + `importlib.util` 조합으로 구현한다. `parsers/` 디렉토리에 파일을 추가하면 `BaseParser.__subclasses__()`가 자동으로 감지한다. 이 패턴은 별도 라이브러리 없이 표준 라이브러리만으로 구현 가능하다.

**Primary recommendation:** Gmail API `history.list` + `messages.modify`(gmail.modify scope) + Python ABC 자동 디스커버리 조합을 사용하라.

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**메일 필터링 전략**
- **D-01:** 발신자 이메일 기반 Gmail API 쿼리로 ASAP 메일 필터링 (from:alerts@acs.org OR from:notify@science.org 등)
- **D-02:** publishers.json 외부 파일에 출판사별 설정 저장 (발신자 이메일, 출판사명, 대표 저널 등). 코드 수정 없이 출판사 추가/수정 가능

**증분 동기화 + 처리 마킹**
- **D-03:** historyId 기반 증분 동기화 — Gmail API history.list로 마지막 실행 이후 변경된 메일만 가져옴. state.json에 historyId 영속화
- **D-04:** 처리 완료 메일에 라벨 부여 — "get-ASAP-processed" 라벨 생성 및 부여. Gmail에서 시각적 확인 가능
- **D-05:** gmail.readonly → gmail.modify로 scope 확장 필요 — Phase 1에서 생성한 token.json 삭제 후 재인증 필요. auth.py의 GMAIL_SCOPES 수정

**저널명 추론 로직**
- **D-06:** publishers.json에 발신자→출판사+저널 매핑 정의. 메일 제목에서 구체적 저널명을 정규식으로 추가 추출

**파서 플러그인 구조**
- **D-07:** parsers/ 디렉토리 자동 디스커버리 — BaseParser 서브클래스를 자동 스캔하여 등록. 파일 추가만으로 새 파서 등록
- **D-08:** PaperMetadata dataclass 반환 — title, doi, journal, date 필드. 타입 안전하고 명확한 데이터 구조

### Claude's Discretion
- state.json의 상세 구조 (historyId 외 추가 필드)
- publishers.json의 상세 스키마 (필수/선택 필드)
- BaseParser 추상 메서드 인터페이스 상세 설계
- 라벨 생성 API 호출 방식 (labels.create)
- 메일 본문 디코딩 방식 (base64, multipart 처리)

### Deferred Ideas (OUT OF SCOPE)
None — discussion stayed within phase scope
</user_constraints>

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| MAIL-01 | Gmail API로 출판사별 ASAP 알림 메일을 필터링하여 가져올 수 있다 (발신자/제목 기반) | `messages.list(q="from:X OR from:Y")` 쿼리 패턴 검증됨 |
| MAIL-02 | historyId 기반 증분 동기화로 새 메일만 처리할 수 있다 (state.json 영속화) | `history.list(startHistoryId=N)` API + 404 폴백 패턴 검증됨 |
| MAIL-03 | 처리 완료된 메일을 READ 상태로 마킹하거나 라벨을 부여할 수 있다 | `messages.modify(addLabelIds=["get-ASAP-processed"])` + `removeLabelIds=["UNREAD"]` |
| PARSE-04 | 출판사별 파서가 모듈화되어 새 출판사 추가가 파일 하나 추가로 가능하다 (Strategy Pattern) | Python ABC + importlib.util + `__subclasses__()` 자동 등록 패턴 검증됨 |
| PARSE-05 | 메일에서 저널명을 자동 추출할 수 있다 (발신자/제목에서 추론) | publishers.json 매핑 + 정규식 제목 파싱 조합 |
</phase_requirements>

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-api-python-client | 2.193.0 (현재) | Gmail API 호출 | Google 공식 Python 클라이언트, 이미 설치됨 |
| google-auth-oauthlib | 1.3.1 (현재) | OAuth 토큰 관리 | 이미 설치됨, Phase 1 auth.py에서 사용 중 |
| python-dotenv | 1.2.2 (현재) | 환경변수 로드 | 이미 설치됨, config.py 패턴 확립됨 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| abc (stdlib) | - | BaseParser 추상 클래스 | 파서 플러그인 인터페이스 정의 |
| importlib.util (stdlib) | - | 동적 모듈 로드 | parsers/ 디렉토리 자동 디스커버리 |
| dataclasses (stdlib) | - | PaperMetadata 구조체 | 파서 반환값 타입 안전성 |
| base64 (stdlib) | - | 메일 본문 디코딩 | Gmail API는 base64url 인코딩으로 반환 |
| json (stdlib) | - | state.json, publishers.json | 영속화 파일 입출력 |
| re (stdlib) | - | 저널명 정규식 추출 | 메일 제목 파싱 |

**신규 설치 불필요** — Phase 2는 Phase 1의 requirements.txt와 표준 라이브러리만으로 구현 가능하다.

## Architecture Patterns

### Recommended Project Structure

```
get-ASAP/
├── auth.py              # 기존 (GMAIL_SCOPES만 수정)
├── config.py            # 기존 (GMAIL_SCOPES 수정)
├── gmail_client.py      # 신규: 메일 필터링, history.list, label 부여
├── parser_registry.py   # 신규: parsers/ 자동 디스커버리 + 등록
├── models.py            # 신규: PaperMetadata dataclass
├── publishers.json      # 신규: 출판사 설정 (D-02)
├── state.json           # 신규: historyId 영속화 (D-03)
├── parsers/
│   ├── __init__.py
│   └── base.py          # BaseParser ABC (D-07)
└── tests/
    ├── test_gmail_client.py
    ├── test_parser_registry.py
    └── test_models.py
```

### Pattern 1: historyId 증분 동기화

**What:** 최초 실행 시 `messages.list()`로 historyId 획득 → 이후 실행에서 `history.list(startHistoryId=N)` 사용
**When to use:** 항상 (MAIL-02)

```python
# Source: https://developers.google.com/workspace/gmail/api/guides/sync
def get_new_messages(service, state: dict, query: str) -> list:
    """증분 동기화로 새 메일 목록 반환. state에서 historyId 로드/저장."""
    history_id = state.get("historyId")

    if not history_id:
        # 최초 실행: 전체 동기화로 historyId 부트스트랩
        result = service.users().messages().list(
            userId="me", q=query, maxResults=1
        ).execute()
        messages = result.get("messages", [])
        if messages:
            msg = service.users().messages().get(
                userId="me", id=messages[0]["id"], format="minimal"
            ).execute()
            state["historyId"] = msg["historyId"]
        return messages

    try:
        response = service.users().history().list(
            userId="me",
            startHistoryId=history_id,
            historyTypes=["messageAdded"],
        ).execute()
        state["historyId"] = response.get("historyId", history_id)
        # 응답에서 messageAdded 목록 추출
        new_ids = []
        for record in response.get("history", []):
            for added in record.get("messagesAdded", []):
                new_ids.append(added["message"]["id"])
        return new_ids
    except HttpError as e:
        if e.resp.status == 404:
            # historyId 만료 — state 초기화 후 재귀 호출로 전체 동기화
            state["historyId"] = None
            return get_new_messages(service, state, query)
        raise
```

### Pattern 2: 발신자 기반 쿼리 생성

**What:** publishers.json의 발신자 목록으로 Gmail 쿼리 문자열 동적 생성
**When to use:** MAIL-01

```python
# Source: Gmail API messages.list q parameter (공식 문서)
def build_query(publishers: dict) -> str:
    """출판사 발신자 이메일로 Gmail 검색 쿼리 생성."""
    senders = [pub["sender"] for pub in publishers.values()]
    return " OR ".join(f"from:{s}" for s in senders)
    # 결과 예: "from:alerts@acs.org OR from:notify@science.org"
```

### Pattern 3: 라벨 get-or-create + 메시지 마킹

**What:** "get-ASAP-processed" 라벨이 없으면 생성, 있으면 기존 ID 사용 → messages.modify로 부여
**When to use:** MAIL-03

```python
# Source: Gmail API labels.list + labels.create + messages.modify (공식 문서)
def get_or_create_label(service, label_name: str) -> str:
    """라벨 ID 반환. 없으면 생성."""
    labels = service.users().labels().list(userId="me").execute()
    for label in labels.get("labels", []):
        if label["name"] == label_name:
            return label["id"]
    created = service.users().labels().create(
        userId="me",
        body={"name": label_name, "labelListVisibility": "labelShow",
              "messageListVisibility": "show"}
    ).execute()
    return created["id"]

def mark_processed(service, message_id: str, label_id: str) -> None:
    """처리 완료 라벨 부여 + UNREAD 제거."""
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "addLabelIds": [label_id],
            "removeLabelIds": ["UNREAD"]
        }
    ).execute()
```

### Pattern 4: BaseParser ABC + 자동 디스커버리

**What:** `abc.ABC` 기반 추상 기저 클래스 + `importlib.util`로 parsers/ 디렉토리 스캔
**When to use:** PARSE-04

```python
# Source: Python stdlib importlib.util + __subclasses__()
# parsers/base.py
from abc import ABC, abstractmethod
from models import PaperMetadata

class BaseParser(ABC):
    """출판사 파서 기저 클래스. 파일 하나 추가로 자동 등록."""
    publisher_name: str = ""   # 구체 클래스에서 오버라이드

    @abstractmethod
    def can_parse(self, sender: str, subject: str) -> bool:
        """이 파서가 해당 메일을 처리할 수 있는지 판단."""

    @abstractmethod
    def parse(self, message_body: str) -> list[PaperMetadata]:
        """메일 본문에서 논문 메타데이터 목록 반환."""

# parser_registry.py
import importlib.util, os

def load_parsers(parsers_dir: str) -> list[BaseParser]:
    """parsers/ 디렉토리의 모든 .py 파일을 로드하여 BaseParser 서브클래스 반환."""
    for fname in os.listdir(parsers_dir):
        if fname.endswith(".py") and not fname.startswith("_"):
            path = os.path.join(parsers_dir, fname)
            spec = importlib.util.spec_from_file_location(fname[:-3], path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
    # 로드 후 __subclasses__()로 자동 감지
    return [cls() for cls in BaseParser.__subclasses__()]
```

### Pattern 5: 메일 본문 base64 디코딩

**What:** Gmail API `format="full"` 응답의 multipart 처리 + base64url 디코딩
**When to use:** 메일 본문 접근 시 (PARSE-05, Phase 3 파서)

```python
# Source: Gmail API message format documentation
import base64

def extract_body(payload: dict) -> str:
    """Gmail 메시지 payload에서 텍스트/HTML 본문 추출."""
    parts = payload.get("parts", [])
    if not parts:
        # 단순 메시지 (multipart 아님)
        data = payload.get("body", {}).get("data", "")
        return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")

    for part in parts:
        mime_type = part.get("mimeType", "")
        if mime_type == "text/html":
            data = part.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        if mime_type == "text/plain":
            data = part.get("body", {}).get("data", "")
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        # 중첩 multipart 재귀 처리
        if mime_type.startswith("multipart/"):
            result = extract_body(part)
            if result:
                return result
    return ""
```

### Pattern 6: PaperMetadata dataclass

**What:** 파서 반환값을 타입 안전하게 정의
**When to use:** PARSE-04, PARSE-05

```python
# models.py
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class PaperMetadata:
    """논문 메타데이터. 파서의 표준 반환 구조."""
    title: str
    doi: str
    journal: str
    date: str
    # 선택 필드
    authors: Optional[list[str]] = field(default=None)
    url: Optional[str] = None
```

### Pattern 7: publishers.json 스키마

**What:** 출판사별 설정 외부 파일 (D-02)

```json
{
  "acs": {
    "sender": "alerts@acs.org",
    "name": "ACS Publications",
    "journals": ["JACS", "ACS Nano", "ACS Catalysis", "Nano Letters"]
  },
  "elsevier": {
    "sender": "ealerts@elsevier.com",
    "name": "Elsevier",
    "journals": ["Applied Catalysis B", "Journal of Catalysis"]
  },
  "science": {
    "sender": "ScienceAdvances@sciencemag.org",
    "name": "Science",
    "journals": ["Science", "Science Advances"]
  }
}
```

### Pattern 8: state.json 스키마

**What:** historyId 영속화 파일 (D-03)

```json
{
  "historyId": "123456789",
  "lastRunAt": "2026-04-03T09:00:00Z",
  "processedCount": 42
}
```

### Anti-Patterns to Avoid

- **historyId=0 사용 금지:** history.list는 historyId=0을 유효하지 않은 값으로 처리. 반드시 최근 메일에서 추출한 실제 ID를 사용해야 한다
- **scope 재사용 금지:** gmail.readonly에서 gmail.modify로 변경 시 기존 token.json이 무효화된다. 코드에서 scope를 변경한 후 반드시 token.json을 삭제하고 재인증해야 한다
- **라벨 ID 하드코딩 금지:** Gmail 라벨 ID는 계정마다 다르다. 항상 get-or-create 패턴으로 동적 조회해야 한다
- **messages.list 로 증분 처리 금지:** `history.list`를 쓰지 않고 매번 `messages.list`로 전체 조회하면 중복 처리 위험이 있고 API 할당량을 낭비한다

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OAuth 토큰 갱신 | 수동 refresh 로직 | google-auth-oauthlib의 `creds.refresh(Request())` | 이미 auth.py에 구현됨 |
| base64 디코딩 | 직접 구현 | `base64.urlsafe_b64decode()` (stdlib) | URL-safe variant 필요, padding 처리 포함 |
| 플러그인 등록 | 중앙 registry 딕셔너리 수동 관리 | `BaseParser.__subclasses__()` + importlib | 파일 추가만으로 자동 등록 |
| 라벨 ID 관리 | 라벨 ID를 .env나 config에 저장 | get-or-create 패턴 (labels.list + labels.create) | 계정 이식성, ID는 계정마다 다름 |
| 히스토리 페이지네이션 | 첫 페이지만 처리 | nextPageToken 루프 | history.list는 최대 500건/페이지, 많은 변경사항 시 다중 페이지 |

## Common Pitfalls

### Pitfall 1: token.json scope 불일치
**What goes wrong:** config.py에서 GMAIL_SCOPES를 `gmail.modify`로 바꿨는데 기존 `gmail.readonly`로 발급된 token.json이 남아있으면 API 호출 시 `403 Insufficient Permission` 오류
**Why it happens:** token.json에는 발급 당시의 scope가 인코딩되어 있어, 코드의 scope 변경이 자동 반영되지 않음
**How to avoid:** GMAIL_SCOPES 변경 후 반드시 token.json 삭제 → 재인증 → 새 token.json 생성 워크플로우 문서화
**Warning signs:** `googleapiclient.errors.HttpError: <HttpError 403>`

### Pitfall 2: historyId 만료 (404 에러)
**What goes wrong:** state.json의 historyId가 일주일 이상 지나거나 특수 상황에서 무효화되면 `history.list()`가 404 반환
**Why it happens:** Gmail API는 히스토리 레코드를 최소 1주일(간혹 수 시간) 보관
**How to avoid:** `HttpError.resp.status == 404` 체크 후 state["historyId"] = None으로 초기화, 전체 동기화 폴백 구현
**Warning signs:** `HttpError 404 Not Found` on `history.list`

### Pitfall 3: base64 padding 오류
**What goes wrong:** Gmail API 응답의 base64url 데이터에 padding('=')이 없어 `base64.b64decode()`가 `binascii.Error: Incorrect padding` 발생
**Why it happens:** base64url 인코딩은 padding을 제거한 채 전송하는 경우가 많음
**How to avoid:** `base64.urlsafe_b64decode(data + "==")` — padding 추가 후 디코딩 (여분 '='는 무시됨)
**Warning signs:** `binascii.Error: Incorrect padding`

### Pitfall 4: history.list에서 messageAdded 없는 응답
**What goes wrong:** `history.list()` 응답에 `history` 키가 없거나, 레코드가 있어도 `messagesAdded` 키가 없는 경우 KeyError
**Why it happens:** 다른 히스토리 타입(labelAdded, labelRemoved 등)의 레코드도 함께 반환될 수 있음
**How to avoid:** `response.get("history", [])` + `record.get("messagesAdded", [])` 방어적 접근
**Warning signs:** `KeyError: 'messagesAdded'`

### Pitfall 5: parsers/ 디스커버리 타이밍 문제
**What goes wrong:** `BaseParser.__subclasses__()`를 importlib 로드 전에 호출하면 빈 목록 반환
**Why it happens:** 서브클래스 등록은 모듈이 import/exec_module 된 후에 발생
**How to avoid:** `load_parsers()` 함수 내에서 먼저 모든 모듈을 로드한 후 `__subclasses__()`를 호출하는 순서 보장
**Warning signs:** `load_parsers()` 반환값이 빈 목록

### Pitfall 6: publishers.json 발신자 이메일 오타
**What goes wrong:** ACS, Elsevier 등의 실제 발신자 이메일이 예상과 다를 수 있음 (noreply@ vs alerts@ 등)
**Why it happens:** 각 출판사의 발신 이메일 주소를 직접 확인해야 함
**How to avoid:** Phase 3 시작 전 실제 수신된 ASAP 메일의 발신자 주소를 확인하여 publishers.json 작성. 현재는 플레이스홀더로 시작
**Warning signs:** messages.list 쿼리 결과가 0건

## Code Examples

### Gmail API 메시지 헤더 추출
```python
# Source: Gmail API messages.get format="metadata" (공식 문서)
def get_message_headers(service, message_id: str) -> dict:
    """메시지 From, Subject, Date 헤더 추출."""
    msg = service.users().messages().get(
        userId="me", id=message_id, format="metadata",
        metadataHeaders=["From", "Subject", "Date"]
    ).execute()
    headers = {h["name"]: h["value"] for h in msg["payload"]["headers"]}
    return headers
    # {"From": "alerts@acs.org", "Subject": "ACS Nano ASAP...", "Date": "..."}
```

### historyId 페이지네이션 처리
```python
# Source: Gmail API history.list nextPageToken (공식 문서)
def list_all_history(service, start_history_id: str) -> list:
    """nextPageToken 루프로 전체 히스토리 레코드 수집."""
    records = []
    page_token = None
    while True:
        kwargs = {"userId": "me", "startHistoryId": start_history_id,
                  "historyTypes": ["messageAdded"]}
        if page_token:
            kwargs["pageToken"] = page_token
        response = service.users().history().list(**kwargs).execute()
        records.extend(response.get("history", []))
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return records
```

### state.json 읽기/쓰기
```python
# 상태 파일 관리
import json, os

STATE_PATH = "state.json"

def load_state() -> dict:
    if os.path.exists(STATE_PATH):
        with open(STATE_PATH) as f:
            return json.load(f)
    return {}

def save_state(state: dict) -> None:
    with open(STATE_PATH, "w") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| IMAP 직접 접근 | Gmail API v1 | 2014+ | OAuth 통합, 검색 기능 |
| messages.list 전체 조회 | history.list 증분 동기화 | Gmail API 초기부터 | API 할당량 절약, 중복 처리 방지 |
| 수동 파서 등록 딕셔너리 | ABC + `__subclasses__()` 자동 등록 | Python 3.4+ (abc 모듈) | 파일 추가만으로 확장 가능 |

## Open Questions

1. **실제 출판사 발신자 이메일 주소**
   - What we know: ACS는 alerts@acs.org 사용 추정, 다른 출판사는 미확인
   - What's unclear: 실제 수신된 ASAP 메일의 정확한 발신자 주소
   - Recommendation: Phase 3 시작 전 실제 메일 샘플을 확인하여 publishers.json을 정확하게 작성. Phase 2에서는 플레이스홀더로 구조 구축에 집중

2. **historyId 부트스트랩 — 첫 실행 시 어떤 메일의 ID를 기준으로 할 것인가**
   - What we know: messages.list의 첫 번째 결과(최신 메일)의 historyId 사용 권장
   - What's unclear: ASAP 쿼리 결과가 0건일 때 어떻게 처리할 것인가
   - Recommendation: 쿼리 결과 0건 시 전체 메일박스 기준으로 historyId 초기화 (q 파라미터 없이 messages.list 1건 조회)

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python | 런타임 | ✓ | 3.14.3 | — |
| google-api-python-client | Gmail API | ✓ | 2.193.0 | — |
| google-auth-oauthlib | OAuth | ✓ | 1.3.1 | — |
| python-dotenv | 환경변수 | ✓ | 1.2.2 | — |
| pytest | 테스트 | ✓ | 8.3.5 | — |
| pytest-mock | Mock | ✓ | 3.14.0 | — |
| credentials.json | Gmail OAuth | ✓ | — | — |
| token.json (gmail.modify) | 라벨 부여 | ✗ | — | 재인증 필요 (token.json 삭제 후 재실행) |

**Missing dependencies with no fallback:**
- `token.json (gmail.modify scope)` — 기존 gmail.readonly 토큰을 삭제하고 재인증 필요. 계획에 명시적 재인증 태스크 포함 필수

**Missing dependencies with fallback:**
- 없음

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest 8.3.5 |
| Config file | 없음 (pytest 기본 설정 사용) |
| Quick run command | `python -m pytest tests/ -x -q` |
| Full suite command | `python -m pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| MAIL-01 | publishers.json 기반 쿼리 문자열 생성 | unit | `python -m pytest tests/test_gmail_client.py::test_build_query -x` | ❌ Wave 0 |
| MAIL-01 | messages.list 필터링 호출 확인 | unit (mock) | `python -m pytest tests/test_gmail_client.py::test_fetch_asap_messages -x` | ❌ Wave 0 |
| MAIL-02 | state.json에서 historyId 로드/저장 | unit | `python -m pytest tests/test_gmail_client.py::test_state_persistence -x` | ❌ Wave 0 |
| MAIL-02 | history.list 404 시 전체 동기화 폴백 | unit (mock) | `python -m pytest tests/test_gmail_client.py::test_history_404_fallback -x` | ❌ Wave 0 |
| MAIL-03 | get-or-create 라벨 로직 | unit (mock) | `python -m pytest tests/test_gmail_client.py::test_get_or_create_label -x` | ❌ Wave 0 |
| MAIL-03 | messages.modify 라벨 부여 호출 | unit (mock) | `python -m pytest tests/test_gmail_client.py::test_mark_processed -x` | ❌ Wave 0 |
| PARSE-04 | parsers/ 디스커버리 자동 등록 | unit | `python -m pytest tests/test_parser_registry.py::test_auto_discovery -x` | ❌ Wave 0 |
| PARSE-04 | BaseParser 추상 메서드 강제 | unit | `python -m pytest tests/test_parser_registry.py::test_abstract_enforcement -x` | ❌ Wave 0 |
| PARSE-05 | publishers.json 발신자→저널명 매핑 | unit | `python -m pytest tests/test_gmail_client.py::test_journal_inference -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `python -m pytest tests/ -x -q`
- **Per wave merge:** `python -m pytest tests/ -v`
- **Phase gate:** Full suite green before `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_gmail_client.py` — MAIL-01, MAIL-02, MAIL-03, PARSE-05 커버
- [ ] `tests/test_parser_registry.py` — PARSE-04 커버
- [ ] `tests/test_models.py` — PaperMetadata dataclass 커버
- [ ] `publishers.json` — 출판사 설정 파일 (플레이스홀더)
- [ ] `state.json` — 초기 상태 파일 (`{}`)
- [ ] `parsers/__init__.py` — 패키지 초기화
- [ ] `parsers/base.py` — BaseParser ABC

## Project Constraints (from CLAUDE.md)

| Directive | Impact on Phase 2 |
|-----------|------------------|
| Python 기반 | gmail_client.py, parser_registry.py, models.py 모두 Python |
| No AI/ML — 정규식 기반 파싱만 | 저널명 추론은 정규식으로만 구현 (PARSE-05) |
| 외부 API 키 하드코딩 금지 | publishers.json은 발신자 이메일만 저장, 키 없음 |
| 모든 답변은 한국어 | 코드 주석 한국어 |
| 이해하기 어려운 코드에는 한국어 주석 | importlib 디스커버리 로직에 주석 필수 |
| TDD (pytest, mock 기반) | 모든 신규 모듈에 단위 테스트 선행 작성 |
| 플랫 프로젝트 구조 | gmail_client.py를 루트에 배치 (src/ 없음) |

## Sources

### Primary (HIGH confidence)
- Gmail API users.history.list — startHistoryId, historyTypes, 404 처리, 페이지네이션
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.history/list
- Gmail API Sync Guide — 전체/증분 동기화 알고리즘, historyId 부트스트랩
  - https://developers.google.com/workspace/gmail/api/guides/sync
- Gmail API messages.list — q 파라미터 문법, from: 쿼리
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list
- Gmail API messages.modify — addLabelIds, removeLabelIds, UNREAD 처리
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify
- Gmail API labels.create — 라벨 생성 요청 구조
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.labels/create
- Gmail API messages.get — format 옵션, multipart 구조, base64url
  - https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get
- Python abc 모듈 공식 문서
  - https://docs.python.org/3/library/abc.html

### Secondary (MEDIUM confidence)
- Gmail API history.list 404 handling — 커뮤니티 확인 (공식 문서와 일치)
  - https://developers.google.com/workspace/gmail/api/guides/sync (재확인)
- Python importlib plugin pattern — GitHub gist + Python Packaging Guide
  - https://gist.github.com/dorneanu/cce1cd6711969d581873a88e0257e312
- Gmail base64url decoding — 복수 소스 확인
  - https://copyprogramming.com/howto/what-is-the-encoding-of-the-body-of-gmail-message-how-to-decode-it

### Tertiary (LOW confidence)
- 실제 출판사 발신자 이메일 주소 — 미검증, Phase 3에서 실제 메일로 확인 필요

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — Phase 1에서 이미 설치됨, 추가 패키지 불필요
- Architecture: HIGH — Gmail API 공식 문서 직접 확인, Python stdlib 패턴
- Pitfalls: HIGH — scope 불일치/historyId 만료/base64 padding은 공식 문서 및 커뮤니티에서 반복 언급
- 발신자 이메일: LOW — 실제 메일 샘플 없이 추정

**Research date:** 2026-04-03
**Valid until:** 2026-05-03 (Gmail API 안정적, 30일)
