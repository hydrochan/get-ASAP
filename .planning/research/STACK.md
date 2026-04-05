# Stack Research

**Domain:** Gmail-to-Notion 자동화 파이프라인 (Python, API 통합)
**Researched:** 2026-04-03
**Confidence:** HIGH

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended |
|------------|---------|---------|-----------------|
| Python | 3.11+ | 런타임 | Gmail/Notion API 공식 클라이언트 지원, 정규식 처리에 최적 |
| google-api-python-client | 2.x | Gmail API 접근 | Google 공식 Python 클라이언트, OAuth 2.0 내장 |
| google-auth-oauthlib | 1.x | OAuth 2.0 인증 흐름 | 토큰 자동 갱신 지원, google-api-python-client 연동 |
| notion-client | 2.x | Notion API 접근 | Notion 공식 Python SDK, DB CRUD 간소화 |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| python-dotenv | 1.x | 환경 변수 관리 | API 키/토큰 하드코딩 방지 |
| beautifulsoup4 | 4.x | HTML 메일 파싱 | 출판사 HTML 메일 파싱 시 정규식 보조 |
| lxml | 4.x | HTML/XML 파서 | BeautifulSoup4 백엔드, 속도 향상 |
| schedule | 1.x | 크론 대안 (로컬) | 개발/테스트 시 간편 스케줄링 |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| venv | 가상환경 | 시스템 Python 오염 방지, 필수 |
| pytest | 단위 테스트 | 파싱 로직 회귀 테스트 |
| cron (Ubuntu) | 프로덕션 스케줄링 | 오라클 클라우드 Ubuntu에서 실행 |

## Installation

```bash
# 가상환경 생성
python3 -m venv venv
source venv/bin/activate

# Core
pip install google-api-python-client google-auth-oauthlib notion-client

# Supporting
pip install python-dotenv beautifulsoup4 lxml

# Dev
pip install pytest
```

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| google-api-python-client | gmail (unofficial) | 공식 SDK가 항상 우선 |
| notion-client (official) | requests (직접 HTTP) | SDK 미지원 엔드포인트 사용 시 |
| cron | Celery/APScheduler | 분산 처리 필요 시 (이 프로젝트는 불필요) |
| BeautifulSoup4 | html.parser (stdlib) | 복잡한 HTML 구조 파싱 시 BS4 권장 |

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| imaplib (직접 IMAP) | Gmail API보다 복잡, OAuth 통합 어려움 | google-api-python-client |
| smtplib | 수신 불가, 발송 전용 | Gmail API read scope |
| 환경변수 하드코딩 | 보안 취약 | python-dotenv + .env 파일 |

## Stack Patterns by Variant

**OAuth 최초 인증 (로컬 실행):**
- 브라우저 기반 OAuth flow → token.json 저장
- 서버 재시작 시 token.json에서 자동 갱신

**서버 환경 (오라클 클라우드, 헤드리스):**
- 로컬에서 token.json 생성 후 서버 복사
- credentials.json + token.json 모두 .env와 분리 관리

## Version Compatibility

| Package A | Compatible With | Notes |
|-----------|-----------------|-------|
| google-api-python-client 2.x | google-auth-oauthlib 1.x | 같은 메이저 버전 유지 |
| notion-client 2.x | Python 3.8+ | 3.11+ 권장 |

## Sources

- Google Gmail API Python Quickstart (공식) — OAuth 2.0, message.list/get
- Notion API 공식 문서 — databases.create, pages.create
- PyPI google-api-python-client — 최신 버전 확인

---
*Stack research for: Gmail-to-Notion 자동화 파이프라인*
*Researched: 2026-04-03*
