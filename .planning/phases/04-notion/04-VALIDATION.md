---
phase: 04
slug: notion
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-05
---

# Phase 04 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | tests/ directory (established in Phase 1-3) |
| **Quick run command** | `python -m pytest tests/ -x -q` |
| **Full suite command** | `python -m pytest tests/ -v` |
| **Estimated runtime** | ~5 seconds |

---

## Sampling Rate

- **After every task commit:** Run `python -m pytest tests/ -x -q`
- **After every plan wave:** Run `python -m pytest tests/ -v`
- **Before `/gsd:verify-work`:** Full suite must be green
- **Max feedback latency:** 5 seconds

---

## Per-Task Verification Map

| Task ID | Plan | Wave | Requirement | Test Type | Automated Command | File Exists | Status |
|---------|------|------|-------------|-----------|-------------------|-------------|--------|
| 04-01-01 | 01 | 1 | NOTION-01 | unit | `python -m pytest tests/test_notion_client.py -v -k create` | ❌ W0 | ⬜ pending |
| 04-01-02 | 01 | 1 | NOTION-02 | unit | `python -m pytest tests/test_notion_client.py -v -k save` | ❌ W0 | ⬜ pending |
| 04-01-03 | 01 | 1 | NOTION-03 | unit | `python -m pytest tests/test_notion_client.py -v -k duplicate` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_notion_client.py` — Notion 클라이언트 테스트 스텁 (DB 생성, 페이지 저장, 중복 검사)

*Existing infrastructure covers test framework requirements.*

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| Notion DB 실제 생성 | NOTION-01 | Notion API 실제 인증 필요 | `python -c "from notion_client_mod import create_paper_db; ..."` 실행 후 Notion에서 DB 확인 |
| 실제 논문 저장 | NOTION-02 | Notion API 실제 인증 필요 | `python -c "from notion_client_mod import save_paper; ..."` 실행 후 Notion에서 페이지 확인 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
