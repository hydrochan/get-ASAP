# Roadmap: get-ASAP

## Overview

Gmail ASAP 알림 메일에서 논문 데이터를 자동 수집하여 Notion DB에 저장하는 단방향 파이프라인을 구축한다. 인증 기반 설정부터 시작하여 메일 감지 프레임워크, 출판사별 파서 구현, Notion 통합, 마지막으로 오라클 클라우드 배포까지 단계적으로 진행한다. 각 단계는 이전 단계가 완성된 후 독립적으로 검증 가능하다.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

- [ ] **Phase 1: 인증 및 환경 설정** - Gmail OAuth 2.0과 Notion Integration Token으로 양쪽 API 연결을 확립한다
- [x] **Phase 2: 메일 감지 프레임워크** - Gmail에서 ASAP 메일을 필터링하고 증분 동기화로 새 메일만 처리하는 기반을 구축한다 (completed 2026-04-03)
- [x] **Phase 3: 출판사 파서 구현** - ACS, Elsevier, Science 출판사 메일에서 논문 제목과 DOI를 추출하는 플러그인 파서를 구현한다 (completed 2026-04-04)
- [ ] **Phase 4: Notion 통합 및 중복 방지** - 추출된 논문 데이터를 Notion DB에 저장하고 DOI 기반 중복을 방지한다
- [ ] **Phase 5: 오라클 클라우드 배포** - cron으로 전체 파이프라인을 자동 실행하고 실행 결과를 로그에 기록한다

## Phase Details

### Phase 1: 인증 및 환경 설정
**Goal**: Gmail API와 Notion API 양쪽에 안정적으로 연결되는 인증 기반이 갖춰진다
**Depends on**: Nothing (first phase)
**Requirements**: AUTH-01, AUTH-02
**Success Criteria** (what must be TRUE):
  1. 로컬에서 OAuth 2.0 흐름을 완료하고 token.json이 생성된다
  2. token.json으로 Gmail API를 호출하면 메일박스 정보를 반환한다 (토큰 만료 시 자동 갱신)
  3. Notion Integration Token으로 API를 호출하면 워크스페이스 정보를 반환한다
  4. .env 파일에 모든 인증 정보가 저장되고 코드에 하드코딩된 키가 없다
**Plans:** 2/2 plans executed
Plans:
- [x] 01-01-PLAN.md — 프로젝트 스캐폴딩 + Gmail OAuth 인증 (AUTH-01)
- [x] 01-02-PLAN.md — Notion 인증 모듈 + 전체 검증 (AUTH-02)

### Phase 2: 메일 감지 프레임워크
**Goal**: 출판사 ASAP 메일만 정확히 필터링하고 이미 처리한 메일은 재처리하지 않는다
**Depends on**: Phase 1
**Requirements**: MAIL-01, MAIL-02, MAIL-03, PARSE-04, PARSE-05
**Success Criteria** (what must be TRUE):
  1. Gmail API 쿼리로 출판사 발신자 기반 ASAP 메일 목록만 가져온다
  2. state.json의 historyId를 사용해 마지막 실행 이후 새 메일만 처리한다
  3. 처리 완료된 메일에 라벨이 부여되거나 READ 상태로 표시된다
  4. 메일 발신자/제목에서 저널명이 자동으로 추론된다
  5. 새 출판사 파서를 parsers/ 디렉토리에 파일 하나 추가하는 것만으로 등록할 수 있다
**Plans:** 2/2 plans complete
Plans:
- [x] 02-01-PLAN.md — 데이터 모델 + 파서 플러그인 구조 (PARSE-04, PARSE-05)
- [x] 02-02-PLAN.md — Gmail 클라이언트 + 증분 동기화 + 라벨 마킹 (MAIL-01, MAIL-02, MAIL-03)

### Phase 3: 출판사 파서 구현
**Goal**: ACS, Elsevier, Science 출판사 ASAP 메일에서 논문 제목과 DOI가 정확히 추출된다
**Depends on**: Phase 2
**Requirements**: PARSE-01, PARSE-02, PARSE-03
**Success Criteria** (what must be TRUE):
  1. ACS ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다
  2. Elsevier ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다
  3. Science ASAP 메일에서 논문 제목과 DOI를 추출하여 PaperMetadata 객체로 반환한다
  4. 파싱에 실패한 메일은 건너뛰고 로그에 기록되며 전체 파이프라인은 계속 실행된다
**Plans:** 2/2 plans complete
Plans:
- [x] 03-01-PLAN.md — 메일 샘플 수집 스크립트 + fixture 확보 + sender 검증 (PARSE-01, PARSE-02, PARSE-03)
- [x] 03-02-PLAN.md — TDD 출판사 파서 구현: ACS, Elsevier, Science (PARSE-01, PARSE-02, PARSE-03)

### Phase 4: Notion 통합 및 중복 방지
**Goal**: 추출된 논문 메타데이터가 Notion DB에 정확하게 저장되고 동일 논문이 중복 저장되지 않는다
**Depends on**: Phase 3
**Requirements**: NOTION-01, NOTION-02, NOTION-03
**Success Criteria** (what must be TRUE):
  1. Notion에 논문 DB가 생성되고 제목, DOI, 저널명, 날짜, 상태 속성이 존재한다
  2. 추출된 논문 데이터가 Notion DB 페이지로 저장되고 상태가 "대기중"으로 설정된다
  3. 동일 DOI를 가진 논문을 재처리해도 Notion DB에 중복 페이지가 생성되지 않는다
**Plans:** 1/2 plans executed
Plans:
- [x] 04-01-PLAN.md — TDD notion_client_mod 구현: DB 생성 + 페이지 저장 + 중복 방지 (NOTION-01, NOTION-02, NOTION-03)
- [ ] 04-02-PLAN.md — 실제 Notion API 통합 테스트 + 사용자 검증 (NOTION-01, NOTION-02, NOTION-03)

### Phase 5: 오라클 클라우드 배포
**Goal**: 오라클 클라우드 Ubuntu에서 파이프라인이 완전 자동으로 주기 실행되고 결과가 로그에 기록된다
**Depends on**: Phase 4
**Requirements**: DEPLOY-01, DEPLOY-02
**Success Criteria** (what must be TRUE):
  1. crontab에 설정된 스케줄로 main.py가 자동 실행된다 (수동 개입 없음)
  2. 각 실행 후 성공/실패/추출 건수가 로그 파일에 기록된다
  3. 서버 재부팅 후에도 cron 작업이 자동으로 재시작된다
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. 인증 및 환경 설정 | 2/2 | Complete |  |
| 2. 메일 감지 프레임워크 | 2/2 | Complete   | 2026-04-03 |
| 3. 출판사 파서 구현 | 2/2 | Complete   | 2026-04-04 |
| 4. Notion 통합 및 중복 방지 | 1/2 | In Progress|  |
| 5. 오라클 클라우드 배포 | 0/TBD | Not started | - |
