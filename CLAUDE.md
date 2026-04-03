<!-- GSD:project-start source:PROJECT.md -->
## Project

**get-ASAP**

Gmail에서 학술 저널 ASAP(As Soon As Published) 알림 메일을 자동으로 감지하여 논문 제목과 DOI를 추출하고, Notion 데이터베이스에 저장하는 자동화 파이프라인. 촉매/에너지 분야 연구자가 최신 논문을 놓치지 않도록 돕는 도구.

**Core Value:** 새로 출판된 논문 정보를 Gmail에서 자동으로 수집하여 Notion DB에 "대기중" 상태로 정확하게 저장하는 것.

### Constraints

- **Tech Stack**: Python 기반 (Gmail API, Notion API 클라이언트)
- **No AI**: AI/ML 없이 정규식 기반 파싱만 사용
- **Runtime**: 오라클 클라우드 Ubuntu에서 cron 실행
- **Auth**: Gmail OAuth 2.0 + Notion Integration Token
- **Cost**: 무료 티어 내에서 운영 (Gmail API, Notion API 모두 무료)
<!-- GSD:project-end -->

<!-- GSD:stack-start source:research/STACK.md -->
## Technology Stack

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
# 가상환경 생성
# Core
# Supporting
# Dev
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
- 브라우저 기반 OAuth flow → token.json 저장
- 서버 재시작 시 token.json에서 자동 갱신
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
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

Conventions not yet established. Will populate as patterns emerge during development.
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

Architecture not yet mapped. Follow existing patterns found in the codebase.
<!-- GSD:architecture-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd:quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd:debug` for investigation and bug fixing
- `/gsd:execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->



<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd:profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
