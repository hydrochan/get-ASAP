# Notion 탈출 설계 (2026-10-23 확정)

## 배경

- 2026-10-21부터 KIST 망(ePrism `[필수] 클라우드_차단`)이 `api.notion.com`·`notion.so`를 차단한다. paper_autodown 일일 실행은 10-20이 마지막 성공이다.
- KIST PC는 `https://get-asap.hydrochan.com`에는 접속된다. Oracle VM은 KIST 밖에 있어 Notion·Gmail에 계속 접속된다.
- **사용자 결정:**
  - 정본을 VM의 SQLite로 옮긴다.
  - Notion 쓰기는 중단하고, 기존 월별 DB는 읽기 전용 보관본으로 동결한다. 미러는 두지 않는다.
  - 검토 UI(Notion 화면에서 강등하던 기능의 대체)는 2단계로 미룬다.
- Notion API를 VM으로 중계해 KIST에 제공하지 않는다. 필수 차단 정책을 우회하는 것이기 때문이다.
- 실측 규모: 서버 CSV 69,065행(2026-04~10, 월 6.6k~10.9k), paper_autodown state.db 70,460행.
- 이 문서는 deep-reasoner·Codex 독립 설계 2건을 종합한 것이고, 두 저장소 구현의 **계약**이다.

## 1. 정본 DB — VM `data/asap.db`

- **경로:** `ASAP_DB_PATH` 환경변수, 기본값 `data/asap.db`. gitignore 대상이다.
- **연결 설정:** WAL, `busy_timeout=10000`, `foreign_keys=ON`. 쓰기는 모두 `BEGIN IMMEDIATE`로 짧은 트랜잭션 안에서 한다.

### `papers` 테이블

| 열 | 정의 |
|---|---|
| `id` | INTEGER PK. 내부용이다. |
| `ref` | TEXT UNIQUE NOT NULL. 외부 식별자다. 이관 행은 Notion page UUID(하이픈 포함 소문자), 신규 행은 uuid4다. 따라서 state.db의 `notion_page_id`가 변환 없이 그대로 조인된다. |
| `title`, `title_key` | TEXT NOT NULL. `title_key`는 현재 `_normalize_title_for_duplicate`와 같은 규칙(NFKC, 대시 통일, 공백, casefold)으로 만든다. |
| `journal`, `url` | TEXT NOT NULL DEFAULT ''. |
| `pub_date` | TEXT NULL, `YYYY-MM-DD`. |
| `bucket_month` | TEXT NOT NULL, `YYYY-MM`. 그 행이 속하는 CSV다. |
| `status` | TEXT NOT NULL DEFAULT '대기중'. CHECK로 대기중/관련/불확실/다운완료/무관/실패만 허용한다. |
| `gpt_reason` | TEXT NOT NULL DEFAULT ''. |
| `status_source` | TEXT NOT NULL. CHECK로 get_asap/paper_autodown/notion_migrated/human만 허용한다. |
| `status_seq` | INTEGER. status가 마지막으로 바뀐 시점의 seq다. |
| `created_at`, `updated_at` | TEXT NOT NULL, ISO UTC `...Z`. 이관 행의 `created_at`은 Notion `created_time`이다. |
| `change_seq` | INTEGER UNIQUE NOT NULL. 동기화 커서다. |
| `deleted_at` | TEXT NULL. tombstone으로 쓰며 행을 물리 삭제하지 않는다. |
| `dup_of` | INTEGER NULL, `papers(id)` 참조. |
| `notion_last_edited` | TEXT NULL. |

**인덱스:**
- `UNIQUE(title_key) WHERE dup_of IS NULL`. tombstone도 포함하므로, 정리해 지운 논문이 다시 들어오지 않는다.
- `bucket_month`, `change_seq`.

### 보조 테이블

- **`status_history`:** `paper_id`, old/new status, old/new source, `seq`, `at`. status가 바뀔 때마다 기록한다(트리거 또는 store 계층). 감사·되돌리기·2단계 준비용이다.
- **`meta`:** key/value. `schema_version`, `last_seq`, `csv_seq:<YYYY-MM>`, `db_epoch`.
  - `db_epoch`는 이 DB 인스턴스의 식별자(uuid4)다. `init_schema`가 없을 때만 만들고, `backup_db.py --restore`가 복원본에 **새로 발급**한다(교체 전 임시 파일에서). 클라이언트가 서버가 되돌려졌음(seq 번호 재사용)을 알아채는 용도다.
  - `csv_seq:<YYYY-MM>`는 `MAX(기존, 새 값)`으로만 기록한다(값이 뒤로 가지 않는다).

### change_seq 규칙

- 추적 열(title, journal, pub_date, url, status, gpt_reason, deleted_at, dup_of)의 값이 **실제로 바뀔 때만**, 같은 쓰기 트랜잭션 안에서 `meta.last_seq`를 +1 해서 부여한다.
- SQLite는 쓰기 주체가 하나이므로 커밋 순서와 seq 순서가 같고, 진행 중인 쓰기 때문에 생기는 빈 번호(gap)가 없다.
- 이 규칙은 한 곳에서만 강제하고 테스트한다(트리거 또는 store.py).

### bucket_month 규칙 (신규 행)

- 발행월이 `2026-04 ≤ pub_month ≤ 이번 달`이면 발행월, 아니면 이번 달이다.
- 기존 `resolve_db_for_month`의 동작(과거월 DB가 없으면 이번 달로 폴백)을 재현한 것이다.
- 이관 행은 원래 Notion DB의 월을 그대로 쓴다.

## 2. 중복 판정

- 키는 전역·전 기간의 `title_key` 하나다. 저널은 키에 넣지 않는다(지금과 같음).
- 메일 1통을 한 트랜잭션으로 처리하고 `INSERT … ON CONFLICT DO NOTHING`을 쓴다. 먼저 들어온 행이 남고, 재알림은 기존 행을 바꾸지 않는다.
- **현재와 달라지는 점:**
  - 3개월 창이 사라진다.
  - exact equals 비교가 정규화 비교로 바뀐다.
  - "재시도 소진 시 중복 아님으로 간주"하던 경로가 사라진다.
  - 그 결과 창 밖 재알림(Early View → 권호 게재)이 다음 달에 행을 하나 더 만들지 않는다.
- Notion에 이미 있던 중복은 이관할 때 모두 보존하고, 두 번째 이후 행에는 `dup_of`를 채운다. 지금 CSV에도 있는 행들이기 때문이다.
- paper_autodown의 재알림 판정 복사(`fetch.py:39`)는 기존 중복 행 처리를 위해 남겨 둔다.

## 3. 일회성 이관 — VM `migrate_from_notion.py`

- **월별 DB 식별:** 부모 페이지 아래 child database를 **전부** 나열해 `get-ASAP YYYY-MM` 제목을 월별로 모으고, 범위 안의 모든 월이 정확히 1개씩일 때만 진행한다. 동명 DB가 2개 이상이거나 한 달이 0개면 어느 쪽이 정본인지 추측하지 않고 중단한다(종료 코드 2, DB·cache 불변).
- **페이지 집합 검증:** DB마다 서로 독립인 두 분할로 page-id 집합을 만들고, 두 집합이 **일치해야** 통과한다. 10k 상한과 조회 누락을 잡기 위해서다.
  - `created_time` 일 단위 창
  - 기존 Date 주 단위 창 + 범위 전·후 + 빈 Date 조회(`notion_fetcher._fetch_month_pages`)
- **필드 규칙:**
  - `ref` = `page.id`
  - `title`, `gpt_reason`: rich_text 조각 전체를 이어 붙인다. 조각이 여럿인 건수를 보고한다.
  - `status`: 비어 있으면 대기중으로 넣고 건수를 보고한다. 모르는 라벨이 있으면 중단한다.
  - `created_at` = `created_time`
  - `bucket_month` = 원래 DB의 월
  - `status_source` = notion_migrated
- **스트리밍:** VM 메모리가 1 GB이므로 메모리에 쌓지 않고 페이지 단위로 upsert한다.
- **재실행 가능:** `ref`로 upsert하되, `status_source ≠ notion_migrated`인 행은 덮어쓰지 않는다.
- **전환 전 통과 조건(gate):**
  1. 두 분할의 집합이 같다.
  2. bucket별 건수가 Notion 건수와 같다.
  3. SQLite에서 export한 CSV가 현재 `cache/*.csv`와 행 multiset으로 비교해 이번 달·지난달은 정확히 같고, 그 이전 달은 차이를 유형별로 분류해 설명할 수 있다.
  4. 모르는 라벨이 0건이다.
  5. 무작위 20행이 Notion과 필드 단위로 일치한다.
- `--dry-run`은 별도 임시 DB에 쓰고 보고서만 낸다.

## 4. get-ASAP 변경

- **`store.py`(신규):**
  - `connect`, `init_schema`
  - `save_papers(papers) -> {saved, skipped, failed}`: 기존 반환 계약과 같다.
  - `export_csv(month)`, `export_dirty()`, `export_all()`, `backup(dest)`
  - verdict 적용·변경 피드 조회 함수
- **`main.py`:** Notion 초기화·저장(:421-443)을 `store.save_papers`로 바꾼다. `_refresh_cache_from_notion`(:148/:465)은 `export_dirty`로 바꾼다. Gmail 체크포인트·라벨 순서(저장 성공 후 라벨)는 그대로 유지한다.
- **CSV export:**
  - 형식: 열·순서·`QUOTE_ALL`·utf-8-sig·제외 저널 드롭·날짜 표기 모두 현재 `_save_cache`와 같게 한다.
  - 원자적 교체: 임시 파일에 쓴 뒤 `os.replace`로 바꿔 서버의 mtime 메모가 그대로 동작하게 한다.
  - 직렬화: 월 export 한 건(스냅샷 읽기 → CSV 쓰기 → `csv_seq` 기록) 전체를 `cache/.export.lock` 배타 잠금(POSIX `fcntl.flock`, Windows는 `msvcrt.locking`, 둘 다 없으면 no-op) 안에서 한다. 대시보드 스레드·cron·main.py가 같은 월을 동시에 export해도 오래된 스냅샷이 나중에 덮어쓰지 못한다.
  - `/api/csv` 계약과 index.html은 변경하지 않는다.
- **`refresh_csv.py`:** `max(change_seq)`가 `csv_seq:<month>`보다 큰 bucket만 export한다. 바뀐 것이 없으면 아무것도 하지 않으므로 cron을 15분 간격으로 돌린다. 현재 crontab에는 이 cron이 없어 새로 등록한다.
- **`/api/refresh_all`(server.py:1056):** export_all을 수행하고 응답 키는 기존과 같게 둔다.
- **Notion 코드:**
  - 운영 경로(main.py, server.py, refresh_csv.py)에서는 Notion을 import하지 않는다.
  - 다시 켜는 플래그는 두지 않는다(정본 두 개는 어긋남을 부른다). 롤백은 git으로 한다.
  - `notion_client_mod.py`·`analytics/notion_fetcher.py`는 이관 스크립트용으로만 남긴다.
- **백업:** `backup_db.py`가 매일 SQLite backup API로 gzip 백업을 만들고 `data/backups/`에 14개를 보관한다. VM 밖 사본은 아래 API로 KIST PC가 매일 받아 간다.

## 5. 동기화 API — `dashboard/sync_api.py`

server.py가 `/api/sync/v1/` prefix 요청을 이 모듈로 넘긴다.

### 공통 규칙

- **인증:**
  - `Authorization: Bearer <ASAP_SYNC_TOKEN>`. 토큰은 VM `.env`에 둔다. 미설정이면 모든 요청에 503을 준다.
  - `hmac.compare_digest`로 비교한다. 실패하면 401 JSON을 주고, 기존 IP 잠금 로직을 재사용한다.
  - **토큰을 먼저 검사한다.** 유효한 토큰은 IP 잠금 여부와 무관하게 통과하고 실패 카운터도 건드리지 않는다(Cloudflare 엣지 IP를 여럿이 공유하므로, 남의 실패로 정상 클라이언트가 잠기거나 정상 요청이 남의 카운터를 지워 브루트포스를 풀어주면 안 된다). 잠금 조회(429)와 실패 카운트 증가는 **인증에 실패한 요청에만** 적용한다.
  - 토큰을 URL이나 로그에 남기지 않는다.
- **요청 형식:** **POST만** 받는다. Cloudflare는 POST를 캐시하지 않는다. 모든 응답에 `Cache-Control: no-store`를 붙인다.
- **본문 상한:** 앱이 `Content-Length > 2 MiB`면 `rfile.read` 전에 413을 준다. nginx에는 `location /api/sync/ { client_max_body_size 2m; … }`를 추가한다. 현재 서버 전체 설정은 256k다.
- **DB가 없으면** 503 `{"error":"db_unavailable"}`.

### 엔드포인트

**`POST /api/sync/v1/ping`** `{}`
→ `{"ok":true, "last_seq":N, "schema_version":1, "db_epoch":"<uuid>"}`

**`POST /api/sync/v1/changes`** `{"after":int, "limit":int}`
- `limit`은 1~1000이고 기본값은 500이다.
- 응답: `{"rows":[row…], "next_after":int, "has_more":bool, "last_seq":N, "db_epoch":"<uuid>"}` (`last_seq`·`db_epoch`는 읽기 스냅샷 기준의 추가 필드다. 구 클라이언트는 무시하면 된다.)
- `row` 필드: `{ref,title,journal,pub_date,url,status,gpt_reason,status_source,status_seq,created_at,bucket_month,deleted,dup_of_ref,seq}`
- 한 번의 읽기 스냅샷에서 `change_seq > after ORDER BY change_seq LIMIT n`으로 읽는다.
- 각 행은 최신 seq 자리에 한 번만 나온다. 나중에 바뀐 행은 더 큰 seq를 받으므로 빠지는 행 없이 최소 1회 전달된다.

**`POST /api/sync/v1/verdicts`** `{"seen_through":int, "items":[{"ref":str, "status":label|null, "gpt_reason":str|null}]}`
- items는 200개 이하다.
- 응답: `{"results":[{"ref", "result":"updated|unchanged|gone|conflict|invalid", "current":row?}], "last_seq":N}`
- 집합(set) 의미라 같은 요청을 재시도해도 결과가 같다. 배치 하나가 트랜잭션 하나다.
- `gone`: 모르는 ref이거나 tombstone인 행.
- `conflict`: `status_source='human'` AND `status_seq > seen_through` AND 요청 status ≠ 현재 status일 때다. 적용하지 않고 `current`를 돌려준다. 2단계 수동 판정을 보호하기 위한 규칙이다.
- status를 적용하면 `status_source=paper_autodown`, `status_seq`=새 seq로 바뀐다.
- status가 그대로여도 **무언가(예: gpt_reason)가 실제로 바뀌고 현재 `status_source`가 `notion_migrated`이면** `status_source=paper_autodown`으로 넘긴다(`status_seq`는 status가 바뀔 때만 바뀐다). 이관 재실행(`upsert_migrated`)이 그 행을 `notion_migrated`로 보고 판정 사유를 덮어쓰지 못하게 하려는 것이다. `human` 출처는 reason만 바뀌는 판정으로는 바꾸지 않는다(status 변경은 위 규칙을 따른다).
- `gpt_reason`은 서버에서 4000자로 자른다. 클라이언트는 지금처럼 1900자로 자른다.

**`POST /api/sync/v1/backup/latest`** `{}`
→ 가장 최근 gzip 백업을 스트리밍한다(`application/gzip`). 응답 헤더 `X-Backup-Name`. 백업이 없으면 404.

### 클라이언트 재시도

- 401/400/413은 재시도하지 않는다.
- 네트워크 오류, 429, 5xx, JSON이 아닌 응답(Cloudflare challenge HTML)은 지수 backoff로 3회 재시도한다. 그래도 실패하면 기존 `notion_sync_pending` 경로가 이어받는다.

## 6. paper_autodown 변경 (이름·계약은 유지하고 구현만 교체 — 이름 정리는 2단계)

- **설정:**
  - `.env`에 `ASAP_SYNC_TOKEN`을 둔다.
  - config에 `asap_api_base`(기본 `https://get-asap.hydrochan.com`)와 선택 항목 `ca_bundle_path`를 추가한다.
  - TLS 검증은 끄지 않는다(`verify=False` 금지).
- **`notion_writer.py`:** `_create_notion_client`가 API 클라이언트를 돌려준다. `update_classified_papers`는 `/verdicts`를 호출하되 `{updated, failed, gone}` 반환은 그대로 둔다. 호출처 8곳은 손대지 않는다. conflict이면 pending을 세우지 않고 로그만 남긴다.
- **`notion/fetch.py` `fetch_papers`:**
  - `/changes`를 페이지 단위로 받아 오되 반환 튜플 형태는 유지한다.
  - "이미 아는 논문" 판정은 ① `ref == notion_page_id`(새 인덱스)를 먼저, ② 그다음 title/journal hash 순서로 한다. 재분류 폭주를 막기 위해서다.
  - 역강등 입력인 `existing_pages`에는 `status_source ∈ {notion_migrated, human}`인 행만 넣는다.
- **커서:**
  - state.db에 `asap_sync(key,value)` 테이블을 두고 서버가 준 `next_after`만 저장한다.
  - 해당 run 처리가 끝난 뒤 커밋한다(pipeline.py:269-281의 월 마감 로직 자리). dry-run에서는 절대 커밋하지 않는다.
  - 그 run에서 push 실패가 하나라도 있으면 커밋하지 않고 다음 run에서 다시 받는다. 다시 받아도 결과는 같다.
  - **서버 복원 감지:** 커서와 함께 서버 `db_epoch`를 같은 트랜잭션으로 저장한다(`asap_sync.db_epoch`). `ping`이 준 epoch가 저장된 값과 **다르거나**, 저장된 커서가 서버 `last_seq`보다 크면 커서를 0으로 되돌려 처음부터 다시 받는다(이미 아는 행은 ref로 건너뛰므로 재분류는 없다). 저장된 epoch가 없으면(첫 run·구 state) 리셋 없이 이번 값을 커서 커밋 때 함께 저장한다. epoch는 커서가 커밋될 때만 저장한다 — 먼저 저장하면 커밋 못 한 run 뒤에 옛 커서가 새 서버에 그대로 쓰여 행을 놓친다.
  - **복원으로 잃은 판정 재push:** ref로 맞는 행에서 서버 `status`가 대기중/빈 값인데 로컬 state에 서버 라벨이 있는 판정(relevant/uncertain/irrelevant/downloaded/failed/permanent_failed)이 있으면, 그 행에 `notion_sync_pending`을 세워 reconcile이 다시 push하게 한다. 서버 `status_source='human'`인 행은 절대 되밀지 않는다. 서버 라벨이 없는 내부 상태(pending/doi_found)는 대상이 아니다. dry-run에서는 표시하지 않는다.
- **`remote_created_at` 열:** 부트스트랩 때 ref로 채우고 upsert 때도 갱신한다. `download/workflow.py:353`은 Notion `pages.retrieve` 대신 이 값을 쓰고, 없으면 오늘 날짜를 쓴다.
- **`pipeline_persist.py:49`:** client/page id가 없으면 바로 return하는 가드를 새 클라이언트 기준으로 바꾼다.
- **Notion 조회를 없애는 곳:**
  - reconcile의 Notion→state 강등 스캔(:88)은 피드가 전 월을 덮으므로 no-op으로 만든다.
  - `review.py`(:282-378)·`manual_import.py`(:241)의 Notion 조회는 "2단계 검토 UI 전까지 비활성" 메시지를 남기고 비활성화한다.
  - 월 마감 로직은 제거한다.
  - heartbeat의 `notion_month_ok`는 `asap_api_ok`로 바꾼다.
- **안전장치:** 한 번 받아 온 결과에서 "신규"가 3,000건을 넘으면 `--allow-large` 없이는 중단한다.
- **VM 밖 백업:** `backup-state` 단계가 `/backup/latest`도 받아 `D:\PaperData\paper_autodown\server_backups\`에 14개를 보관한다.

## 7. 전환 순서

0. **준비:**
   - 코드를 배포하되 비활성 상태로 둔다. DB가 없으면 API가 503을 준다.
   - 양쪽 `.env`에 토큰을 넣고, nginx에 `/api/sync/` location을 추가한다.
   - KIST에서 `ping`을 호출해 Cloudflare challenge 없이 401/200 JSON이 오는지 확인한다.
   - `backup-state`를 실행하고 이관 dry-run을 한다.
1. **VM 동결:** `main.py` cron을 주석 처리하고, 실행 중인 main.py가 끝날 때까지 기다린다. `state.json`과 `cache/`를 백업한다.
2. **이관:** 본 실행 후 §3의 통과 조건을 모두 확인한다.
3. **VM 전환:**
   - CSV export와 diff를 확인하고 대시보드를 재시작한다.
   - cron을 등록한다: 수집 6h, export 15m, 백업 매일.
   - `main.py`를 두 번 실행해 두 번째 실행의 saved가 0인지 확인한다.
   - 동결 중 도착한 메일은 라벨이 없으므로 다음 실행에서 처리된다.
4. **KIST PC 전환:**
   - 작업 스케줄러를 끄고 배포한다.
   - `run --dry-run`으로 부트스트랩 보고를 확인한다. 기대값:
     - 약 68k건 매칭
     - 서버에만 있는 행 ≈ 10-20 이후 수집분
     - state에만 있는 행 = archive된 페이지(gone)
   - 실제 `run` → 전체 .bat 순서로 돌린 뒤 스케줄러를 다시 켠다.
5. **Notion:** 통합의 쓰기 권한을 회수해 읽기 전용으로 둔다(사용자가 수행).

**누락이 없는 이유:**
- 10-20 이후 수집분은 Notion에 있으므로 이관에 포함되고, 커서 0부터 받아 오는 첫 pull에서 분류된다.
- 밀린 판정은 reconcile 단계가 push한다.

**롤백:**
- 3단계 이전: cron을 원래대로 복구한다.
- 3단계 이후: 전환 전 태그로 checkout한다. 필요하면 `created_at > T3`인 행을 기존 `save_papers`로 Notion에 다시 넣는다(중복 판정이 있으므로 안전하다).
- 4단계 이후: 롤백하지 않고 앞으로 고친다. 잘못 들어간 판정은 `status_history`로 되돌린다.

## 8. 먼저 볼 위험

1. 이관 누락: 두 분할 집합 일치 여부.
2. ref 매핑 오류와 그로 인한 재분류 폭주: 부트스트랩 dry-run 수치, 3k 안전장치.
3. 커서 정확성: 페이지를 나눠 받는 도중 insert·update가 일어나는 동시성 테스트.
4. Cloudflare·ePrism이 Python 클라이언트를 막는지.
5. 정본 사본이 하나뿐인 문제: 백업과 **복원**을 둘 다 테스트.
6. CSV 충실도: golden multiset diff.
7. 1 GB VM에서 WAL·lock 대기.

## 9. 배포 메모 (VM — 문서화만, 적용하지 않음)

### 환경변수 (`.env`)

- `ASAP_SYNC_TOKEN=<긴 무작위 문자열>` — 미설정이면 `/api/sync/` 전체가 503이다. 같은 값을 KIST PC의 `.env`에도 둔다.
- `ASAP_DB_PATH=` — 비우면 `data/asap.db`. 같은 폴더의 `backups/`에 일일 백업이 쌓인다.
- 첫 DB가 필요하면 이관 스크립트가 만든다. 이관 없이 빈 DB로 시작하려면 `python store.py --init`.

### nginx — `/api/sync/` location

기존 `location /`(또는 `/api/`)의 `proxy_pass`·`proxy_set_header` 줄을 그대로 복사하고, 아래 두 가지만 다르게 둔다. 서버 전체 `client_max_body_size`(256k)는 그대로 둔다.

```nginx
location /api/sync/ {
    client_max_body_size 2m;                       # 앱도 Content-Length > 2 MiB 이면 읽기 전에 413
    proxy_pass http://127.0.0.1:8501;              # 기존 location 과 같은 upstream
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;       # IP 잠금이 이 헤더를 쓴다 (기존 location 과 동일하게)
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Basic-User "";              # 신뢰 모델: /manage 이외에서는 항상 비운다
    proxy_read_timeout 120s;                       # /backup/latest 스트리밍 여유
}
```

Cloudflare는 POST를 캐시하지 않고 앱이 모든 응답에 `Cache-Control: no-store`를 붙인다. Python 클라이언트에 challenge HTML이 오면 Cloudflare에서 `/api/sync/*` 경로에 대해 Bot Fight/WAF 관리형 챌린지를 건너뛰는 규칙을 둔다(전환 순서 0단계에서 KIST PC로 `ping`을 호출해 401/200 JSON이 오는지 확인).

### crontab (전환 순서 3단계에서 등록 — 수집 6h, export 15m, 백업 매일)

```cron
# get-ASAP 수집 (6시간) - 저장 후 변경된 월 CSV 를 같은 실행에서 export
0 */6 * * * cd /home/ubuntu/get-ASAP && .venv/bin/python main.py >> logs/cron.log 2>&1
# CSV export (15분) - 바뀐 월 bucket 이 없으면 아무것도 하지 않는다 (paper_autodown 판정 반영용)
*/15 * * * * cd /home/ubuntu/get-ASAP && .venv/bin/python refresh_csv.py >> logs/refresh.log 2>&1
# 일일 백업 - data/backups/asap-YYYYMMDD-HHMMSS.db.gz, 최근 14개 보관
30 3 * * * cd /home/ubuntu/get-ASAP && .venv/bin/python backup_db.py >> logs/backup.log 2>&1
```

경로(`/home/ubuntu/get-ASAP`)는 현재 crontab 의 실제 프로젝트 경로로 맞춘다. 복원은 `python backup_db.py --restore <파일.db.gz> --to <새 경로>`(대상이 이미 있으면 거부). 복원본은 `meta.db_epoch`가 새로 발급되므로, 복원 후 첫 `run`에서 KIST PC가 커서를 0으로 되돌리고 서버가 잃은 판정을 reconcile로 다시 push한다.

### 이관 스크립트 운영 메모

- `python migrate_from_notion.py --dry-run` (임시 DB, 5개 gate까지) → `python migrate_from_notion.py` → `python migrate_from_notion.py --verify`.
- gate 3은 이관 시점의 `cache/*.csv`와 비교하므로 §7 1단계(동결) 이후 `cache/`를 건드리기 전에 실행한다.
- 종료 코드: 0 성공, 1 gate 실패, 2 중단(분할 집합 불일치·모르는 라벨·월별 DB가 월마다 정확히 1개가 아님), 3 기타 오류. 보고서는 DB 옆 `migration_report.json`.
