# Phase 4: Notion 통합 및 중복 방지 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-05
**Phase:** 04-notion
**Mode:** auto (all recommended defaults selected)
**Areas discussed:** DB 스키마 설계, 중복 방지 전략, 모듈 구조, 에러 핸들링

---

## DB 스키마 설계

[auto] Selected: PaperMetadata 필드를 Notion 속성으로 직접 매핑 (recommended default)

**Notes:** title→Title, doi→Rich Text, journal→Select, date→Date, 상태→Select("대기중"), url→URL, authors→Rich Text

---

## 중복 방지 전략

[auto] Selected: 저장 전 DOI 쿼리 (recommended default)

**Notes:** databases.query(filter={doi=X}), DOI 없으면 제목 기반 검사

---

## 모듈 구조

[auto] Selected: DB 생성 + 기존 DB 사용 모두 지원 (recommended default)

**Notes:** NOTION_DATABASE_ID 있으면 기존 사용, 없으면 create_paper_db()

---

## 에러 핸들링

[auto] Selected: logging.warning + 스킵 (recommended default)

**Notes:** Phase 3 패턴 유지, rate limit 시 1회 재시도

---

## Claude's Discretion

- Notion API 페이지네이션, parent page 선택, 배치 진행률, .env.example 업데이트

## Deferred Ideas

None
