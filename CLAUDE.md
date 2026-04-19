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

## 운영 가이드

### 새 저널 추가 (같은 출판사)
기존 출판사(ACS, Wiley, Elsevier, Science)의 새 저널은 `publishers.json`의 `journals` 배열에 이름만 추가하면 됨. 파서 코드 수정 불필요.
```json
// 예: Wiley에 "Advanced Science" 추가
"journals": ["Angewandte Chemie", "Advanced Materials", ..., "Advanced Science"]
```

### 새 출판사 추가
1. `publishers.json`에 출판사 항목 추가 (sender, name, journals, doi_prefix)
2. `parsers/` 디렉토리에 파서 파일 생성 (예: `parsers/rsc.py`)
   - `BaseParser`를 상속하고 `can_parse()`, `parse()` 구현
   - 파일 추가만으로 `parser_registry.py`가 자동 등록
3. 실제 메일 HTML을 `tests/fixtures/`에 저장하여 테스트 작성

### DOI prefix 규칙
각 출판사의 DOI는 고유한 prefix를 가짐. `publishers.json`의 `doi_prefix` 필드로 CrossRef 결과를 검증:
- ACS: `10.1021/`
- Wiley: `10.1002/`
- Elsevier: `10.1016/`
- Science: `10.1126/`

### 에러 로그 확인
```bash
# 실패/경고만 필터링
type logs\get-asap.log | findstr "WARNING ERROR"
```
주요 경고 패턴:
- `파서 없음 (publisher=...)` → publishers.json에 출판사 추가 필요
- `CrossRef DOI prefix 불일치` → doi_prefix 확인
- `알 수 없는 발신자` → publishers.json의 sender 확인
- `CrossRef 제목 불일치` → 정상 동작 (엉뚱한 DOI 거부)

### Gmail 토큰 갱신 (token.json 만료 시)
Google OAuth 앱이 production 모드로 전환됨 (2026-04-13). refresh token은 6개월 미사용 시에만 만료.
만약 `RefreshError: Token has been expired or revoked` 에러가 발생하면:

1. 로컬 Windows에서 Python `requests` 라이브러리의 SSL 문제가 있음 (방화벽/보안 설정 충돌)
2. **`get_token_curl.py` 방식으로 우회**: 브라우저 인증 후 curl로 토큰 교환
   ```bash
   # 로컬에서 token.json 재발급 (SSL 문제 우회)
   cd get-ASAP
   python get_token_curl.py
   # 서버에 복사 (서버 정보는 로컬 .env.local 또는 shell profile 참조)
   scp token.json <서버>:~/get-ASAP/token.json
   ```
3. 일반적인 방법이 되면 (`python main.py --dry-run`으로 브라우저 인증) 그걸 써도 됨

### 서버 배포
```bash
ssh <서버>
cd ~/get-ASAP && git pull && .venv/bin/pip install -r requirements.txt
# cron 등록: crontab -e
# 0 */6 * * * cd ~/get-ASAP && .venv/bin/python main.py >> logs/cron.log 2>&1
```

### 대시보드 접속 / 관리

> 실제 서버 IP · 도메인 · SSH 키 파일명 · 내부 포트 · 계정 정보는 **공개 저장소에 기록하지 않음**.
> 운영 세부정보는 로컬 메모장 또는 비공개 노트에 별도 보관.

- 공개 URL: HTTPS (DuckDNS 서브도메인 + Let's Encrypt, certbot 자동 갱신)
- 진입 구조: 외부 HTTPS → Nginx reverse proxy → 로컬 바인드된 Python 서버
- 인증서 갱신 알림: certbot account 이메일 등록

**운영 명령 (서버에서)**
```bash
# 서비스 상태/제어 (systemd로 자동 재시작됨)
sudo systemctl status get-asap-dashboard
sudo systemctl restart get-asap-dashboard
journalctl -u get-asap-dashboard -f    # 실시간 로그

# Nginx 재적재 (TLS 설정 변경 시)
sudo nginx -t && sudo systemctl reload nginx

# 인증서 수동 갱신 테스트 (dry-run)
sudo certbot renew --dry-run
```

**계정 관리**
- 관리자 / 연구실 공용 / 학교 공용 계정 3종 운영 (실제 username / 비밀번호는 공개 저장소에 기록 X)
- 계정 추가·변경: 서버 `~/get-ASAP/.env`의 `DASHBOARD_USERS` JSON 수정 후 `sudo systemctl restart get-asap-dashboard`
- bcrypt 해시 생성: `.venv/bin/python -c "import bcrypt; print(bcrypt.hashpw(b'PASS', bcrypt.gensalt()).decode())"`
- 사용자별 섹션 숨김 / 포커스 프로필 매핑은 `.env`의 `DASHBOARD_USER_PROFILES` 에서 제어

**접속 통계**
- `access_log.db` (SQLite, git 제외) — login/page_view 이벤트 누적
- 관리자 로그인 시 Stats 탭에서 KPI + 사용자별 집계 + 일별 차트 + 최근 이벤트 확인

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
