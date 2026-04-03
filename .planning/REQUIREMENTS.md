# Requirements

**Project:** get-ASAP
**Version:** v1
**Created:** 2026-04-03

## v1 Requirements

### Authentication

- [ ] **AUTH-01**: Gmail OAuth 2.0 인증을 설정하고 token.json으로 자동 갱신할 수 있다 (Production 모드)
- [ ] **AUTH-02**: Notion Integration Token을 설정하고 API 접근을 검증할 수 있다

### Email Detection

- [ ] **MAIL-01**: Gmail API로 출판사별 ASAP 알림 메일을 필터링하여 가져올 수 있다 (발신자/제목 기반)
- [ ] **MAIL-02**: historyId 기반 증분 동기화로 새 메일만 처리할 수 있다 (state.json 영속화)
- [ ] **MAIL-03**: 처리 완료된 메일을 READ 상태로 마킹하거나 라벨을 부여할 수 있다

### Parsing

- [ ] **PARSE-01**: ACS 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다
- [ ] **PARSE-02**: Elsevier 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다
- [ ] **PARSE-03**: Science 출판사 ASAP 메일에서 논문 제목과 DOI를 추출할 수 있다
- [ ] **PARSE-04**: 출판사별 파서가 모듈화되어 새 출판사 추가가 파일 하나 추가로 가능하다 (Strategy Pattern)
- [ ] **PARSE-05**: 메일에서 저널명을 자동 추출할 수 있다 (발신자/제목에서 추론)

### Notion Storage

- [ ] **NOTION-01**: Notion에 논문 DB를 새로 생성할 수 있다 (제목, DOI, 저널명, 날짜, 상태 속성)
- [ ] **NOTION-02**: 추출된 논문 데이터를 Notion DB에 페이지로 저장할 수 있다 (상태="대기중")
- [ ] **NOTION-03**: DOI 기반으로 중복 논문 저장을 방지할 수 있다

### Deployment

- [ ] **DEPLOY-01**: 오라클 클라우드 Ubuntu에서 cron으로 주기적 자동 실행할 수 있다
- [ ] **DEPLOY-02**: 실행 결과를 로그 파일에 기록할 수 있다 (성공/실패/추출 건수)

## v2 Requirements (Deferred)

- [ ] RSC 출판사 파서 추가
- [ ] Wiley 출판사 파서 추가
- [ ] Nature 출판사 파서 추가
- [ ] CrossRef API로 메타데이터 보강 (저자, 연도 등)
- [ ] 알림 통합 (Slack/Telegram)

## Out of Scope

- AI 기반 논문 분류/추천 -- 별도 시스템에서 처리
- 논문 PDF 다운로드 -- 메타데이터만 수집
- 웹 UI 대시보드 -- Notion이 UI 역할
- 실시간 메일 감시 (Push) -- cron 주기 실행으로 충분
- 논문 상태 변경 로직 -- 별도 컴퓨터에서 관심 논문 필터링 후 처리

## Traceability

| REQ-ID | Phase | Status |
|--------|-------|--------|
| AUTH-01 | - | Pending |
| AUTH-02 | - | Pending |
| MAIL-01 | - | Pending |
| MAIL-02 | - | Pending |
| MAIL-03 | - | Pending |
| PARSE-01 | - | Pending |
| PARSE-02 | - | Pending |
| PARSE-03 | - | Pending |
| PARSE-04 | - | Pending |
| PARSE-05 | - | Pending |
| NOTION-01 | - | Pending |
| NOTION-02 | - | Pending |
| NOTION-03 | - | Pending |
| DEPLOY-01 | - | Pending |
| DEPLOY-02 | - | Pending |

---
*15 requirements | 5 categories | Created 2026-04-03*
