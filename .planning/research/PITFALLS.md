# Pitfalls Research

**Domain:** Gmail-to-Notion 자동화 파이프라인
**Researched:** 2026-04-03
**Confidence:** HIGH

## Critical Pitfalls (Must Avoid)

### PITFALL-1: OAuth 토큰 서버 환경 문제
**발생 상황:** 오라클 클라우드(헤드리스 서버)에서 최초 OAuth 인증 시 브라우저 팝업 불가
**결과:** `InsecureTransportError` 또는 인증 루프
**방지 전략:**
- 로컬 PC에서 OAuth 인증 후 생성된 `token.json`을 서버에 SCP로 복사
- `OAUTHLIB_INSECURE_TRANSPORT=1` 환경변수는 개발 전용, 프로덕션에서 사용 금지
- token.json의 refresh_token이 있으면 서버에서 자동 갱신 가능

**Phase 경고:** Phase 1 (인증 구현) — 반드시 로컬 인증 → 서버 배포 워크플로우 문서화

---

### PITFALL-2: 출판사별 메일 형식 미확인으로 파싱 실패
**발생 상황:** 각 출판사(ACS, RSC, Wiley 등)의 HTML 이메일 구조가 모두 다름. 실제 메일 없이 파싱 로직 작성 불가능
**결과:** 전체 파이프라인이 "실행되지만 데이터 없음" 상태
**방지 전략:**
- 최소 1개 출판사(ACS) 실제 메일 수신 후 파서 개발
- 파서를 플러그인 구조로 분리하여 단계적 추가
- 각 파서에 `can_parse()` 메서드로 감지 실패 시 로깅
- 실제 메일 HTML 샘플을 `tests/fixtures/`에 저장하여 회귀 테스트

**Phase 경고:** Phase 2 (파싱 구현) — 실제 메일 수신 대기 필요, 테스트 데이터 확보 선행

---

### PITFALL-3: Notion API Rate Limit 초과
**발생 상황:** 한 번에 많은 논문 저장 시 (ASAP 알림 1통에 50+ 논문 포함 가능)
**결과:** `APIResponseError: rate_limited` (429), 데이터 누락
**방지 전략:**
- 각 Notion API 호출 후 0.3~0.5초 `time.sleep()` 추가
- Rate limit: 약 3 requests/second (Notion 공식 권고)
- 배치 처리 시 큐잉 로직 추가

**Phase 경고:** Phase 3 (Notion 통합) — 대량 데이터 테스트 시 Rate limit 확인

---

## Moderate Pitfalls (Should Avoid)

### PITFALL-4: Gmail API 할당량 초과
**발생 상황:** 너무 잦은 cron 실행 (1분 간격 등)
**결과:** `HttpError 429: quotaExceeded`
**방지 전략:**
- cron 최소 간격: 30분 (Gmail API 무료 할당량 여유 충분)
- `message.list`에 `maxResults` 파라미터 설정 (기본 100, 충분)
- 처리된 메일 ID를 로컬 파일에 기록하여 재조회 방지

---

### PITFALL-5: DOI 정규식 패턴 불일치
**발생 상황:** 출판사마다 DOI 표기 방식이 다름 (`doi:`, `https://doi.org/`, `DOI:` 등)
**결과:** DOI 추출 실패 → 중복 방지 불작동 → Notion에 제목만 저장되거나 중복 저장
**방지 전략:**
- 복수 패턴 사용: `r'(?:doi:|DOI:|https?://doi\.org/)?(10\.\d{4,}/\S+)'`
- DOI 추출 실패 시 논문 저장 자체를 건너뛰고 로그 기록
- 저장 전 DOI 유효성 검증 (10.으로 시작하는지 확인)

---

### PITFALL-6: 이메일 인코딩 문제
**발생 상황:** 일부 메일의 Base64 패딩 오류 또는 멀티파트 MIME 구조 복잡도
**결과:** `UnicodeDecodeError` 또는 빈 본문
**방지 전략:**
- `base64.urlsafe_b64decode(data + '==')` — 패딩 보정
- `errors='replace'` 옵션으로 디코딩 실패 문자 대체
- 텍스트/HTML 파트 우선순위: HTML → plain text 순으로 시도

---

## Minor Pitfalls (Good to Know)

### PITFALL-7: Notion DB ID 관리
**발생 상황:** DB를 수동 생성/삭제하면 `NOTION_DATABASE_ID` 환경변수와 불일치
**방지 전략:** 최초 실행 시 DB ID를 자동 저장하거나, `.env`에서 명시적 관리

### PITFALL-8: cron 환경변수 미로드
**발생 상황:** cron 실행 시 `$HOME/.bashrc`가 로드되지 않아 venv/환경변수 미설정
**방지 전략:**
- crontab에 절대 경로 사용: `/home/ubuntu/get-ASAP/venv/bin/python`
- `.env` 파일은 python-dotenv로 코드 내에서 로드 (셸 환경변수 의존 금지)

### PITFALL-9: token.json 권한 노출
**발생 상황:** token.json이 공개 저장소에 커밋됨
**방지 전략:** `.gitignore`에 `token.json`, `credentials.json`, `.env` 반드시 추가

---

## Phase-Specific Warnings

| Phase | 주요 위험 | 대응 |
|-------|----------|------|
| Phase 1: 인증 설정 | PITFALL-1 (OAuth 서버 문제) | 로컬 인증 후 서버 배포 워크플로우 |
| Phase 2: 파싱 구현 | PITFALL-2 (실제 메일 패턴 미확인) | 실메일 수신 후 개발 |
| Phase 3: Notion 통합 | PITFALL-3 (Rate limit) | sleep 추가 |
| Phase 4: cron 배포 | PITFALL-8 (환경변수 미로드) | 절대경로 + dotenv |

## Sources

- Gmail API 공식 문서 — 할당량 및 에러 코드
- Notion API 공식 문서 — Rate limits 섹션
- OAuth 2.0 헤드리스 서버 패턴 — Google 공식 가이드
- 도메인 경험 — Python API 자동화 일반 패턴

---
*Pitfalls research for: Gmail-to-Notion 자동화 파이프라인*
*Researched: 2026-04-03*
