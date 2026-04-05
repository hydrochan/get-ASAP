# Phase 5: 오라클 클라우드 배포 - Context

**Gathered:** 2026-04-05
**Status:** Ready for planning

<domain>
## Phase Boundary

전체 파이프라인을 main.py로 조합하고, 오라클 클라우드 Ubuntu에서 cron으로 주기적 자동 실행하며, 실행 결과를 로그에 기록한다. 배포 스크립트와 설정 가이드를 포함한다.

</domain>

<decisions>
## Implementation Decisions

### main.py 파이프라인 구성
- **D-01:** 전체 순차 실행 — Gmail 메일 수신 → 출판사별 파싱 → CrossRef DOI 조회 → Notion 저장 → 메일 라벨 마킹
- **D-02:** argparse CLI 옵션 제공:
  - `--dry-run`: Notion 저장 없이 메일 수신 + 파싱만 실행, 결과를 콘솔에 출력
  - `--verbose`: 디버그 레벨 로깅 활성화
- **D-03:** 설정은 .env + publishers.json에서 로드 (기존 패턴 유지)

### cron 스케줄링
- **D-04:** 매 6시간 실행 (config.py의 CHECK_INTERVAL_HOURS=6과 일치)
- **D-05:** crontab 예시: `0 */6 * * * cd /home/ubuntu/get-ASAP && /home/ubuntu/get-ASAP/.venv/bin/python main.py >> logs/cron.log 2>&1`
- **D-06:** 서버 재부팅 후에도 crontab은 자동 유지 (Ubuntu 기본 동작)

### 오라클 클라우드 배포
- **D-07:** git clone으로 서버에 코드 배포, 업데이트는 git pull
- **D-08:** .env와 token.json은 SCP로 별도 전송 (git에 포함 안 됨)
- **D-09:** 서버에서 venv 생성 + pip install -r requirements.txt

### 로깅 전략
- **D-10:** logs/ 디렉토리에 텍스트 형식 로그 파일 (logs/get-asap.log)
- **D-11:** Python logging 모듈 사용 — 파일 + 콘솔 핸들러
- **D-12:** 실행마다 성공/실패/추출 건수 요약 기록 (예: "[2026-04-05 06:00] 완료: 12건 추출, 10건 저장, 2건 중복 스킵")
- **D-13:** --verbose 시 DEBUG 레벨, 기본은 INFO 레벨

### Claude's Discretion
- 로그 로테이션 설정 (RotatingFileHandler 등)
- 배포 스크립트(deploy.sh) 세부 구현
- main.py의 에러 처리 세부사항 (전체 파이프라인 실패 시 exit code)
- cron.log와 get-asap.log 분리 여부

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

No external specs — requirements fully captured in decisions above.

### Project-level
- `.planning/PROJECT.md` — 프로젝트 비전, 제약사항
- `.planning/REQUIREMENTS.md` — DEPLOY-01, DEPLOY-02 요구사항 정의

### Existing Code (필수 참고)
- `auth.py` — get_gmail_service() Gmail 인증
- `gmail_client.py` — build_query, get_new_messages, extract_body, mark_processed, get_or_create_label, infer_journal
- `parser_registry.py` — load_parsers() 파서 자동 디스커버리
- `crossref_client.py` — lookup_doi() DOI 조회
- `notion_client_mod.py` — get_or_create_db, save_papers, save_paper
- `notion_auth.py` — get_notion_client()
- `config.py` — 환경변수 로드
- `publishers.json` — 출판사 설정
- `models.py` — PaperMetadata dataclass

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `auth.py`: get_gmail_service() — Gmail API 서비스 객체
- `gmail_client.py`: 메일 수신/필터링/증분동기화/라벨마킹 전체 기능
- `parser_registry.py`: load_parsers() — 4개 파서 자동 로드
- `crossref_client.py`: lookup_doi() — 제목→DOI 변환
- `notion_client_mod.py`: get_or_create_db(), save_papers() — Notion 저장+중복방지
- `config.py`: .env 환경변수 로드 패턴

### Established Patterns
- TDD (pytest + mock)
- logging.warning + 계속 진행 패턴
- 플랫 프로젝트 구조
- python-dotenv 환경변수 관리

### Integration Points
- main.py가 위 모든 모듈을 조합하여 전체 파이프라인 구성
- cron이 main.py를 주기적으로 실행
- deploy.sh가 서버 초기 설정 자동화

</code_context>

<specifics>
## Specific Ideas

- main.py는 `if __name__ == "__main__":` 패턴으로 직접 실행 가능
- 배포 가이드를 README.md 또는 deploy/ 디렉토리에 문서화
- .env.example에 필요한 모든 환경변수 목록 유지

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-deploy*
*Context gathered: 2026-04-05*
