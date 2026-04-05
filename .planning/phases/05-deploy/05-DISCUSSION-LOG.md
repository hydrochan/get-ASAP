# Phase 5: 오라클 클라우드 배포 - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.

**Date:** 2026-04-05
**Phase:** 05-deploy
**Areas discussed:** main.py 파이프라인, cron 스케줄링, 오라클 배포, 로깅 전략

---

## main.py 파이프라인 구성

| Option | Description | Selected |
|--------|-------------|----------|
| 전체 순차 실행 | Gmail→파싱→DOI→Notion→라벨. 한번에 모든 메일 처리 | ✓ |
| 출판사별 분리 실행 | 각 출판사 독립 처리 | |

**User's choice:** 전체 순차 실행

| Option | Description | Selected |
|--------|-------------|----------|
| 인자 없이 단순 실행 | python main.py 하나로 실행 | |
| argparse 옵션 제공 | --dry-run, --verbose 등 옵션 | ✓ |

**User's choice:** argparse (--dry-run, --verbose)

---

## cron 스케줄링

| Option | Description | Selected |
|--------|-------------|----------|
| 매 6시간 | CHECK_INTERVAL_HOURS=6과 일치, 하루 4회 | ✓ |
| 매일 1회 | API 호출 최소화 | |

**User's choice:** 매 6시간

---

## 오라클 클라우드 배포

| Option | Description | Selected |
|--------|-------------|----------|
| git clone | 서버에서 clone, 업데이트는 pull. .env/token.json은 SCP | ✓ |
| SCP 전체 복사 | 파일 전체 SCP 전송 | |

**User's choice:** git clone

---

## 로깅 전략

| Option | Description | Selected |
|--------|-------------|----------|
| 텍스트 로그 + logs/ | logs/get-asap.log, tail -f로 모니터링 | ✓ |
| JSON 구조화 로그 | 분석 용이, 사람이 읽기 어려움 | |
| stdout만 | cron이 자동 리다이렉트 | |

**User's choice:** 텍스트 로그 + logs/

---

## Claude's Discretion

- 로그 로테이션, deploy.sh 세부사항, exit code, cron.log 분리 여부

## Deferred Ideas

None
