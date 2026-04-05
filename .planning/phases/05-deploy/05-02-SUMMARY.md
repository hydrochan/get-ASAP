---
phase: 05-deploy
plan: "02"
subsystem: deployment
tags: [deploy, cron, env, gitignore]

requires:
  - phase: 05-deploy
    provides: "main.py 파이프라인 오케스트레이터"

provides:
  - "deploy.sh: 오라클 클라우드 Ubuntu 배포 스크립트 (venv, pip, cron 설정)"
  - ".env.example: 전체 환경변수 템플릿"

affects:
  - 운영 환경 배포

tech-stack:
  added: []
  patterns:
    - "bash 배포 스크립트: venv + pip + crontab 자동 설정"

key-files:
  created:
    - deploy.sh
  modified:
    - .env.example
    - .gitignore

key-decisions:
  - "deploy.sh는 bash 스크립트로 venv 생성, pip install, cron 안내 포함"
  - ".gitignore에 logs/, state.json 추가"

patterns-established:
  - "배포: git clone → deploy.sh → SCP(.env, token.json)"

requirements-completed: [DEPLOY-01, DEPLOY-02]

duration: 3min
completed: 2026-04-05
---

# Phase 05 Plan 02: 배포 스크립트 + dry-run 검증 Summary

**deploy.sh 배포 스크립트 작성 + .env.example 업데이트 + main.py --dry-run 실제 검증 완료**

## Performance

- **Duration:** 3 min
- **Tasks:** 2/2 (Task 2는 human-verify)
- **Files modified:** 3

## Accomplishments

- deploy.sh: Python 버전 체크, venv 생성, pip install, logs/ 생성, 인증 파일 체크, crontab 안내
- .env.example: 전체 환경변수 문서화 (GMAIL, NOTION, CHECK_INTERVAL)
- .gitignore: logs/, state.json 추가
- main.py --dry-run --verbose 실제 실행 성공 (18건 메일, 파서 매칭, 저널명 추론 동작 확인)
- 버그 3건 발견 및 수정: publisher 속성명, sender 매칭, parser_map 키 매핑

## Task Commits

1. **Task 1: deploy.sh + .env.example + .gitignore** - `79864f3`
2. **Task 2: dry-run 검증** - approved (버그 수정: `72f9ad3`, `04c919f`)

## Deviations from Plan

- main.py 버그 3건 발견/수정 (publisher→publisher_name, sender 매칭, parser_map 키)
- publishers.json 저널명 보강
- gmail_client.py infer_journal From 헤더 Display Name 폴백 추가

## Self-Check: PASSED
