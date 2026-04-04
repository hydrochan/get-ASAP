---
phase: 03
slug: parser-impl
status: draft
nyquist_compliant: false
wave_0_complete: false
created: 2026-04-05
---

# Phase 03 — Validation Strategy

> Per-phase validation contract for feedback sampling during execution.

---

## Test Infrastructure

| Property | Value |
|----------|-------|
| **Framework** | pytest 7.x |
| **Config file** | tests/ directory (established in Phase 1-2) |
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
| 03-01-01 | 01 | 1 | PARSE-01,02,03 | integration | `python collect_samples.py` | ❌ W0 | ⬜ pending |
| 03-02-01 | 02 | 1 | PARSE-01 | unit | `python -m pytest tests/test_acs_parser.py -v` | ❌ W0 | ⬜ pending |
| 03-02-02 | 02 | 1 | PARSE-02 | unit | `python -m pytest tests/test_elsevier_parser.py -v` | ❌ W0 | ⬜ pending |
| 03-02-03 | 02 | 1 | PARSE-03 | unit | `python -m pytest tests/test_science_parser.py -v` | ❌ W0 | ⬜ pending |

*Status: ⬜ pending · ✅ green · ❌ red · ⚠️ flaky*

---

## Wave 0 Requirements

- [ ] `tests/fixtures/` — 출판사별 실제 메일 HTML fixture 파일 (collect_samples.py로 수집)
- [ ] `tests/test_acs_parser.py` — ACS 파서 테스트 스텁
- [ ] `tests/test_elsevier_parser.py` — Elsevier 파서 테스트 스텁
- [ ] `tests/test_science_parser.py` — Science 파서 테스트 스텁

---

## Manual-Only Verifications

| Behavior | Requirement | Why Manual | Test Instructions |
|----------|-------------|------------|-------------------|
| collect_samples.py 실행 | PARSE-01,02,03 | Gmail API 인증 필요 (실제 계정 접근) | 로컬에서 `python collect_samples.py` 실행 후 tests/fixtures/ 에 HTML 파일 생성 확인 |

---

## Validation Sign-Off

- [ ] All tasks have `<automated>` verify or Wave 0 dependencies
- [ ] Sampling continuity: no 3 consecutive tasks without automated verify
- [ ] Wave 0 covers all MISSING references
- [ ] No watch-mode flags
- [ ] Feedback latency < 5s
- [ ] `nyquist_compliant: true` set in frontmatter

**Approval:** pending
