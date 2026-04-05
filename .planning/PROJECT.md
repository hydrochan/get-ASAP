# get-ASAP

## What This Is

Gmail에서 학술 저널 ASAP(As Soon As Published) 알림 메일을 자동으로 감지하여 논문 제목과 DOI를 추출하고, Notion 데이터베이스에 저장하는 자동화 파이프라인. 촉매/에너지 분야 연구자가 최신 논문을 놓치지 않도록 돕는 도구.

## Core Value

새로 출판된 논문 정보를 Gmail에서 자동으로 수집하여 Notion DB에 "대기중" 상태로 정확하게 저장하는 것.

## Requirements

### Validated

(None yet -- ship to validate)

### Active

- [ ] Gmail API로 ASAP 알림 메일을 주기적으로 감지 (ACS, Science, RSC, Wiley, Nature, Elsevier)
- [ ] 메일 본문에서 논문 제목 + DOI를 정규식으로 추출
- [ ] Notion API로 새 DB 생성 및 논문 데이터 저장 (제목, DOI, 저널명, 날짜, 상태="대기중")
- [ ] Gmail OAuth 2.0 인증 설정 (토큰 자동 갱신 포함)
- [ ] cron으로 주기적 실행 (오라클 클라우드 Ubuntu)
- [ ] 출판사별 메일 파싱 로직 (각 출판사 메일 형식이 다름)
- [ ] 중복 논문 저장 방지 (DOI 기반)

### Out of Scope

- AI 기반 논문 분류/추천 -- 단순 크롤러/파서 역할만 수행
- 논문 상태 변경 로직 -- 별도 시스템에서 처리 (다른 컴퓨터에서 관심 논문 필터링)
- 논문 PDF 다운로드 -- 이 단계에서는 메타데이터만 수집
- 웹 UI -- CLI/cron 기반 실행

## Context

- 대상 출판사: ACS, Science, RSC, Wiley, Nature, Elsevier
- 연구 주제: 수소생산, 암모니아 분해, plasmonic, 불균일촉매 관련 저널
- 메일 패턴 미확인: 실제 ASAP 알림 메일을 수신 후 파싱 로직 개발 필요
- Notion DB 신규 생성 필요
- 배포 환경: 오라클 클라우드 Ubuntu 인스턴스 (SSH 접근 가능)
- 후속 시스템: 다른 컴퓨터에서 Notion DB의 논문 제목을 기반으로 관심 논문을 필터링하는 별도 프로세스 존재 예정

## Constraints

- **Tech Stack**: Python 기반 (Gmail API, Notion API 클라이언트)
- **No AI**: AI/ML 없이 정규식 기반 파싱만 사용
- **Runtime**: 오라클 클라우드 Ubuntu에서 cron 실행
- **Auth**: Gmail OAuth 2.0 + Notion Integration Token
- **Cost**: 무료 티어 내에서 운영 (Gmail API, Notion API 모두 무료)

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Python 사용 | Gmail/Notion API 클라이언트가 잘 갖춰져 있음 | -- Pending |
| 정규식 파싱 | AI 없이 단순 추출, 출판사별 패턴 대응 | -- Pending |
| DOI 기반 중복 방지 | DOI가 논문의 유일한 식별자 | -- Pending |
| 메일 패턴 후발 학습 | 실제 메일 수신 후 파싱 로직 개발 | -- Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd:transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `/gsd:complete-milestone`):
1. Full review of all sections
2. Core Value check -- still the right priority?
3. Audit Out of Scope -- reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-03 after initialization*
