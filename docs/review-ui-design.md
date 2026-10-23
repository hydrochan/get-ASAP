# 2단계: 검토 UI (2026-10-23)

Notion 화면에서 하던 수동 검토를 get-ASAP 대시보드 관리자 화면으로 옮긴다. 정본은 VM SQLite이고, 사람의 판정은 `status_source='human'`으로 기록된다. 이 값은 `docs/notion-exit-design.md` §5 충돌 규칙이 이미 보호하고 있다.

## 0. 기존 Notion 검토 흐름 (대체 대상)

| 사용자가 Notion에서 한 일 | 소비자 (paper_autodown) |
|---|---|
| 불확실/무관/실패 → **관련** 승격 | `review` (paper_autodown_review.bat, 수동 실행): 승격 반영 후 미다운 관련 논문 다운로드 |
| 관련/불확실/실패 → **무관** 강등 | 매일 `run`의 역강등(demotion_sync, 상태만 변경). 이어서 `review`가 피드 경로 PDF와 Zotero를 정리 |
| 관련 → **불확실** 강등 | 역강등(demotion_sync) |
| 직접 받은 PDF → **다운완료** 표시 | `manual-import`: 드롭된 PDF를 제목/PII로 매칭해 등록 |

## 1. 서버 API (get-ASAP `dashboard/server.py`, 관리자 전용)

### 공통 규칙

- **인증:** 기존 `/api/refresh_all`과 같은 관리자 보호 패턴을 쓴다. 세션이 무효면 401, 관리자가 아니면 403.
- **CSRF 방어:** 상태를 바꾸는 요청은 `Origin`(없으면 `Referer`)이 대시보드 자기 origin일 때만 받는다. 세션 쿠키 속성(SameSite)도 확인해서 보고한다.
- **POST만** 받는다. Cloudflare가 `/api/*`의 GET을 캐시할 수 있으므로 목록 조회도 POST로 한다. 모든 응답에 `Cache-Control: private, no-store`를 붙인다.

### `POST /api/admin/review/papers`

**요청:** `{status?: label|"all", month?: "YYYY-MM", q?: str, limit?: ≤500, offset?: int}`
- `status`의 기본값은 불확실이다.
- `month`는 `bucket_month` 기준이다.
- `q`는 제목·저널에 대한 부분일치(대소문자 무시) 검색이다.

**응답:** `{rows:[{ref,title,journal,date,url,status,status_source,gpt_reason,created_at,bucket_month}], total, counts:{label:n}}`
- 정렬은 `created_at DESC`.
- `counts`는 같은 month·q 조건에서 상태별 건수다.
- tombstone과 `dup_of` 행은 제외한다.

### `POST /api/admin/review/status`

**요청:** `{items:[{ref, status}]}`
- items는 200개 이하다.
- `status`는 관련/불확실/무관/다운완료 중 하나다.

**적용:** `store`에 새 함수를 추가해 처리한다. 같은 status여도 `status_source`를 human으로 바꾼다(사람이 확인했다는 표시). status_seq와 change_seq 갱신, status_history(actor=관리자 ID) 기록은 기존 규칙 그대로다.

**응답:** `{results:[{ref,result:"updated|unchanged|gone|invalid", current:row}]}`

판정이 바뀐 월 CSV는 15분 export cron이 다시 만든다. 공개 `/api/csv`는 지금처럼 status 열을 뺀다.

## 2. 대시보드 UI (`dashboard/index.html`)

- **진입:** 관리자에게만 보이는 "Review" 뷰를 기존 Stats 탭처럼 `isAdmin`으로 토글한다. 비관리자가 접근하면 home으로 보낸다.
- **상단 필터:**
  - 상태 칩(각 칩에 `counts` 표시): 불확실(기본) / 관련 / 실패 / 대기중 / 다운완료 / 무관 / 전체
  - 월 선택
  - 검색창
- **목록:** 행마다 다음을 보여 준다.
  - 체크박스
  - 제목: URL을 새 탭으로 연다. `rel="noopener noreferrer"`.
  - 저널, 발행일
  - 현재 상태 배지: 사람이 정한 판정이면 표시를 단다.
  - GPT Reason: 기본 2줄, 클릭하면 펼친다.
  - 버튼 4개: [관련] [불확실] [무관] [다운완료]
- **동작:**
  - 체크한 여러 행에 일괄 적용할 수 있다.
  - 적용 결과를 행에 즉시 반영하고, 실패하면 토스트로 알린다.
  - 페이지네이션은 100행 단위로 "더 보기"를 쓴다.
- **안전:** 모든 문자열은 기존 `esc()`로 이스케이프한다. 기존 Tailwind 스타일을 따른다.

## 3. paper_autodown 재연결

- **state.db 열 추가:** `remote_status`, `remote_status_source`, `remote_status_seq`. 가산·멱등으로 추가한다. `fetch_papers`가 ref·hash로 매칭한 모든 행에 대해 피드 값을 기록한다. dry-run에서는 쓰지 않는다.
- **`review` 명령:** Notion 조회 대신 다음 순서로 동작한다.
  1. **피드 엿보기:** 저장된 커서부터 `/changes`를 받아 매칭 행의 `remote_*`만 갱신한다. **커서는 커밋하지 않는다.** 신규 논문은 run의 몫이므로 손대지 않는다.
  2. **human 판정 적용** (`remote_status_source='human'`):
     - 관련 → state가 uncertain/irrelevant/failed이면 relevant로 승격하고 다운로드 대기열에 넣는다(기존 승격 경로 재사용).
     - 무관/불확실 → 기존 `apply_reverse_demotions` 규칙을 그대로 쓴다(코퍼스 downloaded 보호 포함). 이어서 기존 무관 정리 단계를 수행한다.
- **`manual-import`:** Notion 다운완료 조회 대신 `remote_status='다운완료' AND remote_status_source='human'`이면서 state가 downloaded가 아닌 행을 대상으로 기존 매칭·등록을 수행한다.
- **판정 되돌림 방지:**
  - 승격 후 다운로드에 성공하면 다운완료 push가 나가는데, 이는 정상이다(seen_through ≥ status_seq이므로 충돌이 나지 않는다).
  - 사람이 무관으로 정한 행을 자동 분류가 다시 push해서 덮어쓰지 않는지 확인한다(conflict 규칙).
- review.bat 실행 방식(수동)과 단계 순서(manual-import → review)는 유지한다.

## 4. 범위 밖

- 공개 사용자용 편집
- Notion 재연동
- 대시보드 키워드·차트 변경
