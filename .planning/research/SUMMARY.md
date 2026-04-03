# Project Research Summary

**Project:** get-ASAP
**Domain:** Gmail-to-Notion 학술 논문 자동화 파이프라인
**Researched:** 2026-04-03
**Confidence:** HIGH

## Executive Summary

get-ASAP는 Gmail에서 ACS, RSC, Wiley, Nature, Elsevier, Science 출판사의 ASAP 알림 메일을 주기적으로 감지하여 논문 제목과 DOI를 추출하고 Notion 데이터베이스에 저장하는 단방향 자동화 파이프라인이다. 이런 종류의 프로젝트는 Python + Google API Client + Notion SDK 조합이 사실상 표준이며, 각 API 모두 무료 티어에서 충분히 운용 가능하다. 아키텍처는 단순 선형 파이프라인(Gmail 수신 → 파싱 → Notion 저장)이며, 출판사별 파싱 로직을 플러그인 방식으로 분리하는 것이 핵심이다.

가장 중요한 제약은 실제 ASAP 알림 메일을 수신하기 전까지 출판사별 파싱 로직을 완성할 수 없다는 점이다. 따라서 인증/인프라를 먼저 구축하고, 파싱 로직은 실제 메일 패턴 확인 후 단계적으로 추가하는 방식이 현실적이다. 가장 큰 기술 리스크는 OAuth 2.0 토큰을 헤드리스 서버(오라클 클라우드 Ubuntu)에서 관리하는 것으로, 로컬에서 인증 후 token.json을 서버에 복사하는 패턴으로 해결한다.

전체 개발 난이도는 낮음-중간 수준이며, 4개 Phase(인증 → 파싱 기반 → Notion 통합 → 배포)로 나누면 각 Phase가 독립적으로 테스트 가능하다. 운영 안정성을 위해 멱등성(DOI 기반 중복 방지)과 실패 격리(메일 하나 파싱 실패가 전체 중단을 야기하지 않음)를 설계 원칙으로 삼아야 한다.

## Key Findings

### Recommended Stack

Gmail/Notion 자동화에서 Python 3.11+ + `google-api-python-client` + `notion-client` 조합이 업계 표준이다. 두 라이브러리 모두 공식 SDK이며, OAuth 갱신과 API 추상화가 내장되어 있다. HTML 메일 파싱을 위해 `beautifulsoup4`를 보조로 사용한다.

**Core technologies:**
- `google-api-python-client 2.x`: Gmail API 접근 — Google 공식, OAuth 내장
- `google-auth-oauthlib 1.x`: OAuth 2.0 흐름 — token.json 자동 갱신 지원
- `notion-client 2.x`: Notion API — 공식 SDK, DB/Page CRUD 간소화
- `python-dotenv`: 환경변수 관리 — API 키 하드코딩 방지
- `beautifulsoup4 + lxml`: HTML 메일 파싱 — 출판사 HTML 구조 대응

### Expected Features

**Must have (table stakes):**
- Gmail OAuth 2.0 인증 (token.json 자동 갱신 포함) — API 접근의 전제
- ASAP 메일 감지 (출판사별 발신자 필터) — 핵심 트리거
- 논문 제목 + DOI 추출 — 핵심 데이터
- Notion DB 저장 (제목, DOI, 저널명, 날짜, 상태="대기중") — 최종 목적
- DOI 기반 중복 방지 — 재실행 안전성
- cron 자동 실행 — 완전 자동화

**Should have (competitive):**
- 출판사별 파싱 로직 분리 (ACS, RSC, Wiley, Nature, Elsevier, Science) — 커버리지
- 처리된 메일 ID 추적 — 재실행 안정성
- 실행 로그 파일 — 파싱 실패 디버깅

**Defer (v2+):**
- AI 기반 논문 분류 — 별도 시스템 영역, 이 파이프라인의 범위 초과
- 웹 UI — Notion이 이미 UI 역할 수행
- PDF 자동 다운로드 — 접근 권한 복잡, 현재 불필요

### Architecture Approach

단일 책임 선형 파이프라인 + 출판사별 파서 플러그인 구조. 핵심 컴포넌트는 `gmail_client.py`, `parsers/` 디렉토리(출판사별 파서), `notion_client.py`, `main.py`(오케스트레이터)이며, 모든 설정은 `.env` + `config.py`로 중앙화한다.

**Major components:**
1. `gmail_client.py` — OAuth 인증, 메일 목록 조회, Base64 디코딩
2. `parsers/` (플러그인 디렉토리) — 출판사별 `can_parse()` + `parse()` 구현
3. `notion_client.py` — DOI 중복 체크, 페이지 생성, Rate limit 처리
4. `main.py` — 전체 파이프라인 오케스트레이션, 에러 격리
5. `.env` + `config.py` — 인증 정보 및 설정 중앙화

### Critical Pitfalls

1. **OAuth 토큰 서버 환경 문제** — 로컬에서 인증 후 token.json을 SCP로 서버에 복사하는 워크플로우 사전 문서화
2. **출판사 메일 형식 미확인** — 파서를 플러그인 구조로 설계하고, 실제 메일 수신 후 단계적 구현; `tests/fixtures/`에 실메일 HTML 샘플 저장
3. **Notion API Rate Limit** — 각 API 호출 후 0.3~0.5초 sleep 추가 (약 3 req/s 제한)
4. **DOI 정규식 패턴 불일치** — 복수 패턴 매칭 (`doi:`, `DOI:`, `https://doi.org/`), 추출 실패 시 건너뛰고 로그 기록
5. **cron 환경변수 미로드** — crontab에 절대 경로 사용, python-dotenv로 코드 내에서 .env 로드

## Implications for Roadmap

Based on research, suggested phase structure:

### Phase 1: 인증 및 기반 설정
**Rationale:** 모든 기능이 Gmail OAuth에 의존하므로 최우선 해결. 서버 배포 패턴도 여기서 확정.
**Delivers:** 동작하는 Gmail API 연결, Notion API 연결, 로컬 + 서버 인증 워크플로우
**Addresses:** Gmail OAuth 인증, .env 설정, venv 구성
**Avoids:** PITFALL-1 (OAuth 서버 문제), PITFALL-9 (token.json 노출)

### Phase 2: ACS 파서 구현 (MVP 파서)
**Rationale:** 실제 메일 수신 후 개발 가능한 첫 파서. ACS가 촉매 연구 분야에서 가장 중요한 출판사.
**Delivers:** ACS ASAP 메일 → PaperMetadata 변환 동작 검증
**Uses:** beautifulsoup4, 정규식 DOI 추출, pytest fixtures
**Avoids:** PITFALL-2 (패턴 미확인), PITFALL-5 (DOI 정규식 불일치)

### Phase 3: Notion 통합 및 중복 방지
**Rationale:** 데이터 저장 목적지. ACS 파서 완성 후 end-to-end 파이프라인 완성.
**Delivers:** Notion DB 신규 생성, 논문 페이지 저장, DOI 중복 방지 동작
**Implements:** notion_client.py, main.py 오케스트레이션
**Avoids:** PITFALL-3 (Rate limit), PITFALL-7 (DB ID 관리)

### Phase 4: 출판사 파서 확장
**Rationale:** RSC, Wiley, Nature, Elsevier, Science 순으로 실제 메일 수신 시마다 추가.
**Delivers:** 6개 출판사 전체 커버리지
**Uses:** 플러그인 파서 구조 (Phase 2에서 확립된 패턴 반복)
**Note:** 출판사별로 실메일 수신 후 개발, 별도 PR/커밋으로 단계적 추가

### Phase 5: 오라클 클라우드 배포 및 cron 설정
**Rationale:** 로컬 동작 검증 후 프로덕션 배포. cron 환경 특수성 해결.
**Delivers:** 완전 자동화 파이프라인, 30분 간격 자동 실행, 실행 로그
**Avoids:** PITFALL-8 (cron 환경변수), PITFALL-4 (Gmail API 할당량)

### Phase Ordering Rationale

- OAuth가 모든 것의 전제 → Phase 1이 반드시 먼저
- 파서는 실메일 수신 전에 완성 불가 → Phase 2는 실메일 수신 대기 후 시작
- Notion 통합은 파싱 검증 후 연결하는 것이 디버깅 용이 → Phase 3이 Phase 2 다음
- 출판사 확장은 독립적 반복 작업 → Phase 4는 어느 시점에든 추가 가능
- 배포는 모든 로컬 동작 검증 후 → Phase 5가 마지막

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2:** 출판사별 실제 메일 HTML 구조 미확인 — 첫 메일 수신 후 파싱 패턴 연구 필요
- **Phase 5:** 오라클 클라우드 Ubuntu cron 환경 — 서버 접근 후 환경변수/경로 확인 필요

Phases with standard patterns (skip research-phase):
- **Phase 1:** OAuth 2.0 흐름은 Google 공식 문서에 잘 정의됨
- **Phase 3:** Notion API CRUD는 공식 SDK로 표준화됨
- **Phase 4:** Phase 2에서 확립된 파서 패턴 반복

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | Google/Notion 공식 SDK, 변경 가능성 낮음 |
| Features | HIGH | PROJECT.md 요구사항 명확, 범위 잘 정의됨 |
| Architecture | HIGH | 단순 선형 파이프라인, 검증된 패턴 |
| Pitfalls | HIGH | OAuth 서버 패턴, Rate limit은 반복적으로 보고되는 이슈 |

**Overall confidence:** HIGH

### Gaps to Address

- **출판사 메일 파싱 패턴:** 실제 ASAP 알림 메일을 수신하기 전까지 파싱 로직 검증 불가 — Phase 2 시작 전 메일 샘플 수집 필요
- **오라클 클라우드 SSH/cron 환경:** 실제 서버 환경에서 경로/권한/환경변수 검증 필요 — Phase 5에서 해결
- **Notion DB parent_page_id:** 어느 페이지 하위에 DB를 생성할지 미결정 — Phase 3에서 사용자 확인 필요

## Sources

### Primary (HIGH confidence)
- Google Gmail API Python 공식 문서 — OAuth 2.0, messages.list/get, 할당량
- Notion API 공식 문서 (developers.notion.com) — databases.create, pages.create, rate limits
- PyPI notion-client, google-api-python-client — 최신 버전 및 호환성

### Secondary (MEDIUM confidence)
- PROJECT.md — 프로젝트 요구사항 및 제약
- Google OAuth 2.0 헤드리스 서버 패턴 — 공식 가이드 기반 추론

### Tertiary (LOW confidence)
- 출판사별 ASAP 메일 HTML 구조 — 실제 수신 전 검증 불가, 실메일 기반 업데이트 필요

---
*Research completed: 2026-04-03*
*Ready for roadmap: yes*
