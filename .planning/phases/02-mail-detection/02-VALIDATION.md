---
phase: 2
slug: mail-detection
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-03
---

# Phase 2 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | tests/conftest.py (existing) |
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
| 2-01-01 | 01 | 1 | MAIL-01 | unit | `python -m pytest tests/test_gmail_client.py -x -v` | ❌ W0 | ⬜ pending |
| 2-01-02 | 01 | 1 | MAIL-02 | unit | `python -m pytest tests/test_state_manager.py -x -v` | ❌ W0 | ⬜ pending |
| 2-01-03 | 01 | 1 | MAIL-03 | unit | `python -m pytest tests/test_label_manager.py -x -v` | ❌ W0 | ⬜ pending |
| 2-02-01 | 02 | 2 | PARSE-04 | unit | `python -m pytest tests/test_parser_registry.py -x -v` | ❌ W0 | ⬜ pending |
| 2-02-02 | 02 | 2 | PARSE-05 | unit | `python -m pytest tests/test_journal_inference.py -x -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/test_gmail_client.py` — stubs for MAIL-01
- [ ] `tests/test_state_manager.py` — stubs for MAIL-02
- [ ] `tests/test_label_manager.py` — stubs for MAIL-03
- [ ] `tests/test_parser_registry.py` — stubs for PARSE-04
- [ ] `tests/test_journal_inference.py` — stubs for PARSE-05

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| gmail.modify scope 재인증 | MAIL-03 | 브라우저 기반 Google 재로그인 | 1. token.json 삭제 2. verify_gmail.py 실행 3. 브라우저 인증 4. 라벨 생성 확인 |
| 실제 ASAP 메일 필터링 | MAIL-01 | 실제 메일 수신 필요 | 1. gmail_client.py로 메일 검색 2. 결과 확인 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
