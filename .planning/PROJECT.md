# get-ASAP

## What This Is

Gmail에서 학술 저널 ASAP 알림 메일을 자동으로 감지하여 논문 제목과 저널명을 추출하고, Notion 데이터베이스에 "대기중" 상태로 저장하는 자동화 파이프라인. 촉매/에너지 분야 연구자가 최신 논문을 놓치지 않도록 돕는 도구.

## Core Value

새로 출판된 논문 정보를 Gmail에서 자동으로 수집하여 Notion DB에 정확하게 저장하는 것.

## Requirements

### Validated

- Gmail OAuth 2.0 인증 + 토큰 자동 갱신 -- v1.0
- Notion Integration Token 인증 -- v1.0
- Gmail API 출판사별 ASAP 메일 필터링 (발신자 기반) -- v1.0
- historyId 기반 증분 동기화 (새 메일만 처리) -- v1.0
- 처리 완료 메일 라벨 마킹 -- v1.0
- 출판사별 파서 플러그인 구조 (파일 추가로 자동 등록) -- v1.0
- ACS, Wiley, Elsevier, Science 4개 출판사 파서 -- v1.0
- Notion DB 생성 + 논문 페이지 저장 (상태="대기중") -- v1.0
- 제목 기반 중복 저장 방지 -- v1.0
- cron 매 6시간 자동 실행 -- v1.0
- 실행 결과 로그 기록 -- v1.0

### Active

(v2에서 정의 예정)

### Out of Scope

- AI 기반 논문 분류/추천 -- 단순 파서 역할만 수행
- 논문 PDF 다운로드 -- 메타데이터만 수집
- 웹 UI -- Notion이 UI 역할
- 실시간 메일 감시 (Push) -- cron 주기 실행으로 충분
- DOI 조회 -- ASAP 논문은 CrossRef 미등록이 많아 제목만으로 충분

## Context

- 대상 출판사: ACS, Wiley, Elsevier, Science (v1.0)
- v2 후보: RSC, Nature
- 배포 환경: 오라클 클라우드 Ubuntu (SSH, cron)
- 기술 스택: Python 3.12, google-api-python-client, notion-client 3.0.0, BeautifulSoup4
- 코드: 3,225 LOC Python, 85개 테스트
- 후속 시스템: 다른 컴퓨터에서 Notion DB의 논문 제목을 기반으로 관심 논문 필터링 예정

## Constraints

- **Tech Stack**: Python 기반 (Gmail API, Notion API)
- **No AI**: BeautifulSoup4 기반 HTML 파싱만 사용
- **Runtime**: 오라클 클라우드 Ubuntu에서 cron 실행
- **Auth**: Gmail OAuth 2.0 + Notion Integration Token
- **Cost**: 무료 티어 내에서 운영

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python 사용 | Gmail/Notion API 클라이언트 지원 | ✓ Good |
| BeautifulSoup4 HTML 파싱 | 정규식보다 구조적 파싱에 강건 | ✓ Good |
| DOI 조회 제거 | ASAP 논문은 CrossRef 미등록 다수, 엉뚱한 DOI 반환 문제 | ✓ Good |
| 제목 기반 중복 방지 | DOI 없이도 제목으로 충분히 중복 검출 | ✓ Good |
| publishers.json 외부 설정 | 코드 수정 없이 출판사/저널 추가 가능 | ✓ Good |
| notion-client 3.0.0 | data_sources.query API 필수, databases.query 제거됨 | ⚠️ 마이그레이션 주의 |

---
*Last updated: 2026-04-05 after v1.0 milestone*
