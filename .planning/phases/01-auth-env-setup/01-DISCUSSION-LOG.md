# Phase 1: 인증 및 환경 설정 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-03
**Phase:** 01-인증 및 환경 설정
**Areas discussed:** 프로젝트 구조, OAuth 인증 설정, 환경 변수 구성, 검증 스크립트

---

## 프로젝트 구조

### 패키지 관리 도구

| Option | Description | Selected |
|--------|-------------|----------|
| pip + requirements.txt | 단순하고 오라클 클라우드 Ubuntu 배포 시 호환성 최고 | ✓ |
| Poetry | pyproject.toml 기반, 의존성 lock 파일 생성 | |
| uv | Rust 기반 초고속 패키지 매니저 | |

**User's choice:** pip + requirements.txt
**Notes:** 추천 옵션 선택, 배포 환경 호환성 우선

### 디렉토리 구조

| Option | Description | Selected |
|--------|-------------|----------|
| 플랫 구조 | 루트에 main.py + parsers/ + utils/ | ✓ |
| src/ 패키지 구조 | src/get_asap/ 하위에 모든 코드 | |
| Claude에게 맡김 | Claude가 판단 | |

**User's choice:** 플랫 구조
**Notes:** 작은 프로젝트에 적합한 단순 구조 선택

---

## OAuth 인증 설정

### Credential 타입

| Option | Description | Selected |
|--------|-------------|----------|
| Desktop App | 로컬 브라우저 인증 후 token.json 생성 | ✓ |
| Service Account | JSON 키 파일로 직접 인증 | |

**User's choice:** Desktop App
**Notes:** 사용자가 Google Cloud Console의 역할에 대해 질문함. Google Cloud Console은 API 접근 권한 설정, 오라클 클라우드는 코드 실행 서버임을 설명.

### Gmail API Scope

| Option | Description | Selected |
|--------|-------------|----------|
| gmail.readonly | 메일 읽기만 가능, 안전 | ✓ |
| gmail.modify | 읽기 + 라벨 부여/READ 상태 변경 | |
| Claude에게 맡김 | Claude가 판단 | |

**User's choice:** gmail.readonly
**Notes:** Phase 2에서 라벨 부여 필요 시 scope 확장 가능

---

## 환경 변수 구성

| Option | Description | Selected |
|--------|-------------|----------|
| 단일 .env | 인증 + 설정 모두 하나의 .env에 저장 | ✓ |
| .env + config.json 분리 | 인증은 .env, 실행 설정은 config.json | |
| Claude에게 맡김 | Claude가 판단 | |

**User's choice:** 단일 .env
**Notes:** 작은 프로젝트에 단순명확한 구성 선호

---

## 검증 스크립트

| Option | Description | Selected |
|--------|-------------|----------|
| verify_auth.py 단일 스크립트 | Gmail + Notion 한 번에 검증 | |
| 개별 검증 스크립트 | verify_gmail.py, verify_notion.py 분리 | ✓ |
| Claude에게 맡김 | Claude가 판단 | |

**User's choice:** 개별 검증 스크립트
**Notes:** 각 API를 독립적으로 검증 가능하도록 분리

---

## Claude's Discretion

- token.json 자동 갱신 구현 세부 사항
- config.py의 python-dotenv 사용 방식
- .gitignore 구성

## Deferred Ideas

None — discussion stayed within phase scope
