# Feature Research

**Domain:** Gmail-to-Notion 자동화 파이프라인
**Researched:** 2026-04-03
**Confidence:** HIGH

## Feature Landscape

### Table Stakes (Users Expect These)

Features users assume exist. Missing these = product feels incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Gmail OAuth 2.0 인증 | API 접근의 전제 조건 | MEDIUM | token.json 자동 갱신 포함 |
| ASAP 알림 메일 감지 | 핵심 트리거 | LOW | 출판사별 발신자/제목 필터링 |
| 논문 제목 추출 | 핵심 데이터 | MEDIUM | 출판사별 HTML/텍스트 패턴 상이 |
| DOI 추출 | 논문 고유 식별자 | MEDIUM | 정규식: `10.\d{4,}/\S+` 패턴 |
| Notion DB에 저장 | 최종 목적지 | LOW | title, DOI, 저널명, 날짜, 상태="대기중" |
| 중복 방지 (DOI 기반) | 동일 논문 중복 저장 방지 | MEDIUM | Notion DB에서 DOI 쿼리 후 확인 |
| cron 자동 실행 | 수동 실행 불필요 | LOW | 오라클 클라우드 Ubuntu 크론탭 |

### Differentiators (Competitive Advantage)

Features that set the product apart. Not required, but valuable.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| 출판사별 파싱 로직 분리 | ACS/RSC/Wiley/Nature/Elsevier/Science 각각 대응 | HIGH | 실제 메일 수신 후 패턴 확인 필요 |
| 저널명 자동 추출 | Notion에서 필터링 용이 | MEDIUM | 메일 제목/발신자에서 추론 |
| 실행 로그 기록 | 파싱 실패 추적 | LOW | 텍스트 파일 또는 stdout 로깅 |
| 처리된 메일 ID 추적 | 재실행 시 중복 처리 방지 | LOW | 로컬 파일 또는 Gmail 라벨 활용 |

### Anti-Features (Commonly Requested, Often Problematic)

| Feature | Why Requested | Why Problematic | Alternative |
|---------|---------------|-----------------|-------------|
| AI 기반 논문 분류 | 관심 논문 자동 필터링 | 범위 초과, 별도 시스템에서 처리 예정 | 단순 저장 후 별도 프로세스에서 처리 |
| PDF 자동 다운로드 | 논문 즉시 열람 | DOI 기반 접근 권한 복잡, 범위 초과 | 메타데이터만 저장, PDF는 수동 |
| 웹 UI 대시보드 | 시각화 편의 | 개발 비용 대비 Notion이 이미 UI 역할 | Notion DB를 UI로 활용 |
| 실시간 메일 감시 | 즉각 알림 | Gmail Push Notification 복잡, 불필요 | 10-30분 cron으로 충분 |

## Feature Dependencies

```
[Gmail OAuth 인증]
    └──requires──> [Gmail API 메일 감지]
                       └──requires──> [출판사별 파싱 로직]
                                          └──requires──> [Notion DB 저장]
                                                             └──requires──> [중복 방지]

[처리된 메일 ID 추적] ──enhances──> [Gmail API 메일 감지]
[저널명 추출] ──enhances──> [Notion DB 저장]
[실행 로그] ──enhances──> [출판사별 파싱 로직]
```

### Dependency Notes

- **Gmail OAuth가 모든 것의 전제:** 인증 없이 API 호출 불가, Phase 1에서 반드시 해결
- **파싱 로직은 실제 메일 수신 후 개발 가능:** 패턴 미확인이므로 유연한 아키텍처 필요
- **중복 방지는 Notion DB 저장에 의존:** DB가 없으면 중복 체크 불가

## MVP Definition

### Launch With (v1)

- [ ] Gmail OAuth 2.0 인증 + token.json 자동 갱신
- [ ] ACS 출판사 ASAP 메일 감지 및 파싱 (가장 많이 사용)
- [ ] 논문 제목 + DOI 추출
- [ ] Notion DB 신규 생성 + 페이지 저장 (제목, DOI, 저널명, 날짜, 상태="대기중")
- [ ] DOI 기반 중복 방지
- [ ] cron 자동 실행 설정

### Add After Validation (v1.x)

- [ ] RSC, Wiley, Nature, Elsevier, Science 파싱 추가 — 실제 메일 수신 후 패턴 확인
- [ ] 처리된 메일 ID 추적 (로컬 파일) — 재실행 안정성 향상
- [ ] 실행 로그 파일 — 파싱 실패 디버깅

### Future Consideration (v2+)

- [ ] 저널별 관심도 점수 — 별도 시스템 영역
- [ ] 알림 통합 (슬랙/텔레그램) — 현재는 Notion으로 충분

## Feature Prioritization Matrix

| Feature | User Value | Implementation Cost | Priority |
|---------|------------|---------------------|----------|
| Gmail OAuth 인증 | HIGH | MEDIUM | P1 |
| ASAP 메일 감지 | HIGH | LOW | P1 |
| 논문 제목/DOI 추출 | HIGH | MEDIUM | P1 |
| Notion DB 저장 | HIGH | LOW | P1 |
| DOI 중복 방지 | HIGH | MEDIUM | P1 |
| 출판사별 파싱 확장 | HIGH | HIGH | P1 (단계적) |
| 처리 메일 ID 추적 | MEDIUM | LOW | P2 |
| 실행 로그 | MEDIUM | LOW | P2 |
| 저널명 추출 | MEDIUM | MEDIUM | P2 |

## Sources

- Gmail API 공식 문서 — message.list, message.get, label 필터링
- Notion API 공식 문서 — databases, pages CRUD
- 프로젝트 PROJECT.md — 요구사항 분석

---
*Feature research for: Gmail-to-Notion 자동화 파이프라인*
*Researched: 2026-04-03*
