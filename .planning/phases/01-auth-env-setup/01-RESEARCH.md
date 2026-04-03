# Phase 1: 인증 및 환경 설정 - Research

**Researched:** 2026-04-03
**Domain:** Gmail API OAuth 2.0, Notion API Integration Token, Python 환경 설정
**Confidence:** HIGH

---

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AUTH-01 | Gmail API OAuth 2.0 인증 구현 (token.json 생성, 자동 갱신) | InstalledAppFlow 패턴, credentials.json 설정, Credentials 갱신 로직 확인 완료 |
| AUTH-02 | Notion Integration Token 인증 구현 (.env 환경 변수 기반) | notion-client 3.x SDK, users/me 엔드포인트로 연결 검증 방법 확인 완료 |
</phase_requirements>

---

## Summary

Gmail API는 OAuth 2.0 InstalledAppFlow 방식을 사용한다. Google Cloud Console에서 "데스크톱 앱" 유형의 OAuth 클라이언트를 생성하고 credentials.json을 다운로드한 뒤, 최초 실행 시 브라우저 인증을 거쳐 token.json을 생성한다. 이후 실행 시에는 token.json에서 자동으로 자격증명을 로드하고 만료 시 refresh_token으로 자동 갱신된다.

Notion API는 훨씬 단순하다. Notion 통합(Integration) 대시보드에서 Internal Integration을 생성하면 비밀 토큰(NOTION_TOKEN)이 발급된다. notion-client 3.0.0 SDK로 `Client(auth=os.environ["NOTION_TOKEN"])`만 호출하면 즉시 사용 가능하다. 연결 검증은 `notion.users.me()` 엔드포인트로 수행한다.

모든 인증 정보(NOTION_TOKEN, Gmail credentials.json 경로 등)는 python-dotenv를 통해 .env 파일에서 로드하며, .env와 token.json, credentials.json은 .gitignore에 반드시 추가해야 한다.

**Primary recommendation:** Gmail은 InstalledAppFlow + token.json 자동갱신 패턴, Notion은 notion-client SDK + 환경변수 패턴을 사용한다.

---

## Project Constraints (from CLAUDE.md)

- 외부 API 키는 절대 코드에 하드코딩 금지
- 모든 답변은 한국어로
- 에러 발생 시 즉시 중단 후 보고
- 이해하기 어려운 코드에는 한국어 주석 추가
- git push 전 반드시 먼저 물어볼 것

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| google-auth | 2.49.1 | Google 자격증명 관리, 토큰 갱신 | 공식 Google 라이브러리 |
| google-auth-oauthlib | 1.3.1 | OAuth 2.0 InstalledAppFlow | 공식 Google 라이브러리 |
| google-api-python-client | 2.193.0 | Gmail API 서비스 빌드/호출 | 공식 Google 라이브러리 |
| notion-client | 3.0.0 | Notion API SDK (공식) | ramnes/notion-sdk-py 공식 래퍼 |
| python-dotenv | 1.2.2 | .env 파일 환경변수 로드 | 이미 설치됨, 12-factor app 표준 |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| httpx | >=0.23.0 | notion-client 의존성 | notion-client 설치 시 자동 |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| notion-client (공식) | requests + 직접 HTTP 호출 | requests는 불필요한 보일러플레이트가 많음, 공식 SDK 권장 |
| python-dotenv | os.environ 직접 설정 | .env 파일 없이 개발 불편, dotenv가 표준 |

**Installation:**
```bash
pip install google-auth google-auth-oauthlib google-api-python-client notion-client python-dotenv
```

**Version verification:** 위 버전은 2026-04-03 기준 `pip3 index versions` 및 PyPI로 검증됨.

---

## Architecture Patterns

### Recommended Project Structure
```
project-root/
├── .env                    # 환경변수 (gitignore)
├── .env.example            # 환경변수 템플릿 (git 포함)
├── .gitignore              # token.json, credentials.json, .env 포함
├── credentials.json        # Google OAuth 클라이언트 비밀 (gitignore)
├── token.json              # OAuth 토큰 (자동생성, gitignore)
├── auth/
│   ├── gmail_auth.py       # Gmail OAuth 인증 로직
│   └── notion_auth.py      # Notion 클라이언트 초기화
└── requirements.txt        # 의존성 목록
```

### Pattern 1: Gmail OAuth 2.0 InstalledAppFlow
**What:** 최초 실행 시 브라우저 인증 → token.json 저장 → 이후 자동 로드/갱신
**When to use:** 데스크톱/로컬 앱에서 사용자 Gmail에 접근할 때
**Example:**
```python
# Source: https://developers.google.com/workspace/gmail/api/quickstart/python
import os.path
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

# 스코프 변경 시 token.json 삭제 필요
SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

def get_gmail_service():
    """Gmail API 서비스 객체 반환 (인증 포함)"""
    creds = None
    
    # 저장된 토큰이 있으면 로드
    if os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json", SCOPES)
    
    # 유효한 자격증명이 없으면 인증 진행
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 만료된 토큰 자동 갱신
            creds.refresh(Request())
        else:
            # 최초 실행: 브라우저로 OAuth 인증
            flow = InstalledAppFlow.from_client_secrets_file(
                "credentials.json", SCOPES
            )
            creds = flow.run_local_server(port=0)
        
        # 토큰 저장 (다음 실행 시 재사용)
        with open("token.json", "w") as token:
            token.write(creds.to_json())
    
    return build("gmail", "v1", credentials=creds)
```

### Pattern 2: Notion Client 초기화 및 연결 검증
**What:** 환경변수에서 토큰 로드 → 클라이언트 생성 → users/me로 검증
**When to use:** Notion API 연결이 필요한 모든 모듈
**Example:**
```python
# Source: https://github.com/ramnes/notion-sdk-py
import os
from notion_client import Client, APIResponseError
from dotenv import load_dotenv

load_dotenv()  # .env 파일 로드

def get_notion_client():
    """Notion API 클라이언트 반환 (연결 검증 포함)"""
    token = os.environ.get("NOTION_TOKEN")
    if not token:
        raise ValueError("NOTION_TOKEN 환경변수가 설정되지 않았습니다")
    
    notion = Client(auth=token)
    return notion

def verify_notion_connection(notion: Client) -> dict:
    """Notion API 연결 검증 - bot user 정보 반환"""
    try:
        # GET /v1/users/me - 봇 사용자 및 워크스페이스 정보
        bot_info = notion.users.me()
        return bot_info
    except APIResponseError as e:
        raise ConnectionError(f"Notion API 연결 실패: {e}")
```

### Pattern 3: .env 파일 구성
**What:** 모든 인증 정보를 환경변수로 관리
**Example:**
```bash
# .env.example (git에 포함 - 실제 값 없이 키만 명시)
NOTION_TOKEN=your_notion_integration_token_here
GMAIL_CREDENTIALS_PATH=credentials.json
GMAIL_TOKEN_PATH=token.json
```

### Anti-Patterns to Avoid
- **토큰 하드코딩:** `notion = Client(auth="secret_abc123")` — CLAUDE.md 위반, 절대 금지
- **스코프 변경 후 token.json 미삭제:** 스코프 변경 시 token.json을 삭제하지 않으면 권한 오류 발생
- **credentials.json git 커밋:** Google OAuth 클라이언트 비밀이 노출됨
- **token.json git 커밋:** 사용자 액세스 토큰이 노출됨
- **port 고정 없는 run_local_server:** `port=0`은 임의 포트 사용 (권장), 고정 포트는 Cloud Console에도 등록 필요

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| OAuth 토큰 갱신 로직 | 직접 refresh_token 처리 | `creds.refresh(Request())` | 만료 감지, 재시도, 에러 처리 복잡 |
| HTTP 요청에 Authorization 헤더 추가 | `requests` + 직접 헤더 설정 | `notion-client` SDK | Rate limiting, 버전 헤더, 에러 파싱 자동 처리 |
| .env 파일 파싱 | 직접 파일 읽기/파싱 | `python-dotenv` | 주석, 따옴표, 멀티라인 처리 필요 |
| OAuth flow 브라우저 리다이렉트 처리 | 직접 HTTP 서버 구현 | `InstalledAppFlow.run_local_server()` | 로컬 서버 생성, 코드 교환, 토큰 저장 자동 처리 |

**Key insight:** Google Auth 라이브러리가 토큰 만료/갱신/저장을 모두 처리하므로 직접 구현 불필요.

---

## Common Pitfalls

### Pitfall 1: Gmail API 테스트 사용자 미등록 (access_denied)
**What goes wrong:** "Access denied: app has not been verified" 또는 "access_denied" 오류
**Why it happens:** Google Cloud Console OAuth 동의 화면에서 앱 게시 상태가 "테스트"일 때, 등록된 테스트 사용자만 인증 가능
**How to avoid:** Google Cloud Console > OAuth 동의 화면 > "테스트 사용자" 섹션에 본인 Google 계정 이메일 추가
**Warning signs:** 브라우저에서 "앱이 확인되지 않음" 경고 후 진행 불가

### Pitfall 2: 스코프 변경 후 token.json 미삭제
**What goes wrong:** 이미 다른 스코프로 발급된 token.json이 있으면 새 스코프 적용 안 됨
**Why it happens:** `Credentials.from_authorized_user_file()` 로드 시 저장된 스코프 그대로 사용
**How to avoid:** SCOPES 변경 시 token.json 삭제 후 재인증 (공식 문서 주석에도 명시됨)
**Warning signs:** `insufficient_scope` 오류 또는 API 호출 권한 오류

### Pitfall 3: Notion 데이터베이스/페이지 통합 미연결
**What goes wrong:** `object_not_found` 또는 `Unauthorized` 오류
**Why it happens:** Notion Integration을 생성해도, 접근할 페이지/DB에 해당 Integration을 명시적으로 연결해야 함
**How to avoid:** Notion 페이지/DB > "..." 메뉴 > "연결" > 생성한 Integration 선택
**Warning signs:** 토큰은 유효하나 특정 페이지/DB 접근 시 403/404

### Pitfall 4: credentials.json 경로 오류
**What goes wrong:** `FileNotFoundError: credentials.json not found`
**Why it happens:** 스크립트 실행 디렉토리와 credentials.json 위치 불일치
**How to avoid:** `GMAIL_CREDENTIALS_PATH` 환경변수로 절대경로 또는 프로젝트 루트 기준 상대경로 지정
**Warning signs:** 다른 디렉토리에서 스크립트 실행 시에만 오류 발생

### Pitfall 5: Gmail API 테스트 사용자 토큰 7일 만료
**What goes wrong:** 7일 후 refresh_token이 무효화됨
**Why it happens:** 미검증 앱의 테스트 사용자 인증은 7일 유효
**How to avoid:** 개인 프로젝트이므로 허용 범위 내, 만료 시 token.json 삭제 후 재인증
**Warning signs:** `invalid_grant` 오류

---

## Code Examples

### Gmail API 연결 검증 (메일박스 라벨 목록 조회)
```python
# Source: https://developers.google.com/workspace/gmail/api/quickstart/python
from googleapiclient.errors import HttpError

def verify_gmail_connection(service) -> bool:
    """Gmail API 연결 검증 - 라벨 목록 조회"""
    try:
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        print(f"Gmail 연결 성공: {len(labels)}개 라벨 확인")
        return True
    except HttpError as error:
        print(f"Gmail API 오류: {error}")
        return False
```

### Notion users.me 연결 검증
```python
# Source: https://developers.notion.com/reference/get-self
def verify_notion_connection(notion) -> str:
    """Notion API 연결 검증 - 워크스페이스 이름 반환"""
    bot = notion.users.me()
    workspace_name = bot.get("bot", {}).get("workspace_name", "Unknown")
    print(f"Notion 연결 성공: 워크스페이스 '{workspace_name}'")
    return workspace_name
```

### .gitignore 필수 항목
```
# 인증 파일 (절대 커밋 금지)
.env
token.json
credentials.json

# Python
__pycache__/
*.pyc
.venv/
```

---

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| `flow.run_console()` | `flow.run_local_server(port=0)` | 2020+ | 브라우저 자동 열기, 코드 붙여넣기 불필요 |
| `oauth2client` (deprecated) | `google-auth` + `google-auth-oauthlib` | 2019 | oauth2client 지원 종료됨 |
| Notion unofficial Python API | `notion-client` (공식 SDK) | 2021 | 공식 지원, 안정적 API |

**Deprecated/outdated:**
- `oauth2client`: Google이 공식 지원 종료, `google-auth`로 대체
- `notion-py` (jamalex/notion-py): 비공식 라이브러리, 현재 유지보수 중단

---

## Open Questions

1. **Gmail API 스코프 범위**
   - What we know: `gmail.readonly`는 읽기 전용, 이메일 목록/본문 읽기 가능
   - What's unclear: Phase 2+ 에서 메일 전송/수정이 필요한지 (현재 Phase 1은 연결 검증만)
   - Recommendation: Phase 1에서는 `gmail.readonly`로 시작, 필요 시 확장

2. **Notion Integration 권한 범위**
   - What we know: Internal Integration은 워크스페이스 전체가 아닌 연결된 페이지/DB만 접근
   - What's unclear: 어떤 Notion 페이지/DB에 Integration을 연결할지 (사용자 설정 필요)
   - Recommendation: Phase 1 계획 시 "테스트용 Notion 페이지에 Integration 연결" 태스크 포함

---

## Environment Availability

| Dependency | Required By | Available | Version | Fallback |
|------------|------------|-----------|---------|----------|
| Python 3.x | 모든 스크립트 | ✓ | 3.14.3 | — |
| pip | 패키지 설치 | ✓ | 25.3 | — |
| python-dotenv | .env 로드 | ✓ | 1.2.2 (설치됨) | — |
| google-auth | Gmail OAuth | ✗ (미설치) | — | pip install로 설치 |
| google-auth-oauthlib | Gmail OAuth | ✗ (미설치) | — | pip install로 설치 |
| google-api-python-client | Gmail API | ✗ (미설치) | — | pip install로 설치 |
| notion-client | Notion API | ✗ (미설치) | — | pip install로 설치 |
| 브라우저 | Gmail OAuth 최초 인증 | ✓ | Windows 기본 | — |
| Google Cloud 프로젝트 | credentials.json | 사용자 설정 필요 | — | 수동 생성 필요 |
| Notion Integration | NOTION_TOKEN | 사용자 설정 필요 | — | 수동 생성 필요 |

**Missing dependencies with no fallback:**
- Google Cloud Console 프로젝트 생성 및 Gmail API 활성화 (수동 작업)
- credentials.json 다운로드 (Google Cloud Console에서 수동 다운로드)
- Notion Integration 생성 및 NOTION_TOKEN 발급 (Notion 대시보드에서 수동 생성)

**Missing dependencies with fallback:**
- google-auth 등 Python 패키지: `pip install` 태스크로 해결

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | pytest (표준, 별도 설치 필요) |
| Config file | pytest.ini 또는 pyproject.toml (Wave 0 생성) |
| Quick run command | `pytest tests/test_auth.py -x -v` |
| Full suite command | `pytest tests/ -v` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| AUTH-01 | token.json 생성 및 Gmail API 호출 성공 | integration | `pytest tests/test_gmail_auth.py -x` | ❌ Wave 0 |
| AUTH-01 | 토큰 만료 시 자동 갱신 | unit (mock) | `pytest tests/test_gmail_auth.py::test_token_refresh -x` | ❌ Wave 0 |
| AUTH-02 | Notion API 호출로 워크스페이스 정보 반환 | integration | `pytest tests/test_notion_auth.py -x` | ❌ Wave 0 |
| AUTH-02 | .env에서 토큰 로드, 코드 하드코딩 없음 | unit | `pytest tests/test_notion_auth.py::test_env_loading -x` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `pytest tests/ -x -q` (빠른 스모크 테스트)
- **Per wave merge:** `pytest tests/ -v` (전체 테스트)
- **Phase gate:** 전체 테스트 통과 후 `/gsd:verify-work`

### Wave 0 Gaps
- [ ] `tests/test_gmail_auth.py` — AUTH-01 Gmail 인증 테스트 (실제 API 호출 + mock 갱신 테스트)
- [ ] `tests/test_notion_auth.py` — AUTH-02 Notion 인증 테스트 (실제 API 호출 + 환경변수 테스트)
- [ ] `tests/conftest.py` — 공유 픽스처 (mock credentials, 환경변수 설정)
- [ ] Framework install: `pip install pytest pytest-mock` (미설치 시)

---

## Sources

### Primary (HIGH confidence)
- [Google Gmail API Python Quickstart](https://developers.google.com/workspace/gmail/api/quickstart/python) - InstalledAppFlow 코드 패턴, 토큰 갱신 로직
- [googleapis/google-api-python-client OAuth Installed](https://googleapis.github.io/google-api-python-client/docs/oauth-installed.html) - run_local_server vs run_console
- [Notion API Reference - Get Self](https://developers.notion.com/reference/get-self) - users/me 엔드포인트, 응답 구조
- [ramnes/notion-sdk-py GitHub](https://github.com/ramnes/notion-sdk-py) - 설치, 초기화, 에러 처리 패턴
- `pip3 index versions` - 패키지 버전 직접 확인 (2026-04-03)

### Secondary (MEDIUM confidence)
- [Google OAuth 2.0 Scopes](https://developers.google.com/identity/protocols/oauth2/scopes) - Gmail API 스코프 목록
- [Notion Authorization Docs](https://developers.notion.com/docs/authorization) - Internal Integration 토큰 사용법
- [Google Cloud - Unverified Apps](https://support.google.com/cloud/answer/7454865) - 테스트 사용자 설정 방법

### Tertiary (LOW confidence)
- 없음

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH - pip registry에서 직접 버전 확인, 공식 라이브러리
- Architecture: HIGH - 공식 Google 퀵스타트 코드 패턴 직접 확인
- Pitfalls: HIGH - 공식 문서 + 실제 Google 지원 문서에서 확인된 일반적인 문제들

**Research date:** 2026-04-03
**Valid until:** 2026-07-03 (90일 - Google/Notion API는 비교적 안정적)
