# Phase 2: 메일 감지 프레임워크 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-03
**Phase:** 02-메일 감지 프레임워크
**Areas discussed:** 메일 필터링 전략, 증분 동기화 + 처리 마킹, 저널명 추론 로직, 파서 플러그인 구조

---

## 메일 필터링 전략

### Gmail API 쿼리 방식

| Option | Description | Selected |
|--------|-------------|----------|
| 발신자 기반 쿼리 | 출판사별 발신자 이메일로 필터링 | ✓ |
| 발신자 + 제목 키워드 | 발신자 + subject에 ASAP/Just Published 키워드 조합 | |
| Claude에게 맡김 | 실제 메일 패턴 조사 후 결정 | |

**User's choice:** 발신자 기반 쿼리

### 출판사 설정 저장 위치

| Option | Description | Selected |
|--------|-------------|----------|
| config.py에 하드코딩 | 딕셔너리로 정의. 단순하지만 코드 수정 필요 | |
| publishers.json 외부 파일 | JSON 파일에 분리. 코드 수정 없이 추가/수정 가능 | ✓ |
| Claude에게 맡김 | 프로젝트 규모에 맞는 방식 결정 | |

**User's choice:** publishers.json 외부 파일

---

## 증분 동기화 + 처리 마킹

### 처리 완료 표시 방식

| Option | Description | Selected |
|--------|-------------|----------|
| 라벨 부여 (gmail.modify) | "get-ASAP-processed" 라벨. scope 확장 필요 | ✓ |
| state.json에 ID 기록 | readonly 유지 가능하지만 ID 리스트 성장 | |
| 둘 다 병행 | state.json + 라벨. scope 확장 필요 | |

**User's choice:** 라벨 부여
**Notes:** gmail.readonly → gmail.modify scope 확장 필요. token.json 재생성 필요.

### 증분 동기화 방식

| Option | Description | Selected |
|--------|-------------|----------|
| historyId 기반 | history.list API로 변경분만 가져옴 | ✓ |
| 날짜 기반 | after:{date} 쿼리로 필터링 | |
| Claude에게 맡김 | 기술적 트레이드오프 고려 후 결정 | |

**User's choice:** historyId 기반

---

## 저널명 추론 로직

| Option | Description | Selected |
|--------|-------------|----------|
| publishers.json 매핑 | 발신자→출판사+저널 매핑 + 제목에서 추가 추출 | ✓ |
| 제목 파싱만 | 정규식으로 제목에서 저널명 추출 | |
| Claude에게 맡김 | 출판사 메일 패턴 조사 후 결정 | |

**User's choice:** publishers.json 매핑

---

## 파서 플러그인 구조

### 등록 방식

| Option | Description | Selected |
|--------|-------------|----------|
| 자동 디스커버리 | parsers/ 디렉토리 스캔, BaseParser 서브클래스 자동 등록 | ✓ |
| publishers.json에 명시적 등록 | 파서 클래스명을 JSON에 매핑 | |
| Claude에게 맡김 | Strategy Pattern 구현 방식 결정 | |

**User's choice:** 자동 디스커버리

### 반환 데이터 구조

| Option | Description | Selected |
|--------|-------------|----------|
| PaperMetadata dataclass | title, doi, journal, date 필드. 타입 안전 | ✓ |
| 단순 dict | 딕셔너리 반환. 유연하지만 타입 불명확 | |
| Claude에게 맡김 | 적절한 데이터 구조 결정 | |

**User's choice:** PaperMetadata dataclass

---

## Claude's Discretion

- state.json 상세 구조
- publishers.json 상세 스키마
- BaseParser 추상 메서드 인터페이스
- 라벨 생성 API 호출 방식
- 메일 본문 디코딩 방식

## Deferred Ideas

None — discussion stayed within phase scope
