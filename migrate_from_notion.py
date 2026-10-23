#!/usr/bin/env python
"""일회성 이관: Notion 월별 DB -> 정본 SQLite (Notion 탈출 설계 §3). VM에서 실행한다(KIST 망은 Notion 차단).

이 스크립트만 Notion 모듈(analytics.notion_fetcher / notion_client_mod)을 import한다. 운영 경로(main.py,
server.py, refresh_csv.py, store.py)는 Notion을 import하지 않는다.

흐름
  1. 월별 DB마다 서로 독립인 두 분할로 page-id 집합을 만든다.
       A: created_time 일 단위 창        (페이지를 받으면서 바로 upsert - 메모리에 쌓지 않는다)
       B: 기존 Date 주 단위 창 + 범위 전·후 + 빈 Date (notion_fetcher._fetch_month_pages와 같은 필터)
     두 집합이 일치해야 통과한다(10k 상한·조회 누락 탐지). 불일치/모르는 상태 라벨은 즉시 중단한다.
  2. ref(=page id)로 upsert. status_source != notion_migrated 인 행은 덮어쓰지 않는다(재실행 안전).
  3. 보고서(JSON)를 쓴다. --verify 가 §3의 5개 gate를 점검한다.

사용법
  python migrate_from_notion.py --dry-run          # 임시 DB로 리허설 + 5개 gate까지 실행(운영 DB·cache 불변)
  python migrate_from_notion.py                    # 본 실행 (ASAP_DB_PATH 또는 data/asap.db)
  python migrate_from_notion.py --verify           # 본 실행 후 gate 1~5 점검 (보고서·DB·cache/*.csv·Notion 표본)
  옵션: --db PATH  --report PATH  --cache-dir DIR  --start 2026-04  --end YYYY-MM  --sample 20  --seed N
        --skip-notion-sample (gate 5 생략: Notion에 닿지 않는 환경에서 gate 1~4만 볼 때)

종료 코드: 0 성공 / 1 gate 실패 / 2 중단(집합 불일치·모르는 라벨·월별 DB가 월마다 정확히 1개가 아님) / 3 기타 오류
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone

import store

logger = logging.getLogger("migrate_from_notion")

NOTION_VERSION = "2022-06-28"
NOTION_QUERY_URL = "https://api.notion.com/v1/databases/{}/query"
NOTION_PAGE_URL = "https://api.notion.com/v1/pages/{}"
NOTION_CHILDREN_URL = "https://api.notion.com/v1/blocks/{}/children"
_DB_TITLE_RE = re.compile(r"^get-ASAP (\d{4}-\d{2})$")
WINDOW_CAP = 9000  # 한 필터 창이 이만큼 돌려주면 10k 상한에 근접한 것으로 보고 중단한다
SAMPLE_LIMIT = 200  # 보고서에 남기는 ref 목록 길이 상한
CSV_FIELD_NAMES = ("title", "journal", "date", "url", "status", "gpt_reason")


class MigrationAbort(Exception):
    """이관을 이어갈 수 없는 상황(집합 불일치, 모르는 라벨, 월별 DB가 0개·2개 이상 등)."""


class PartitionMismatch(MigrationAbort):
    pass


class UnknownStatusLabel(MigrationAbort):
    pass


class AmbiguousMonthDatabase(MigrationAbort):
    pass


def _month_range(start: str, end: str) -> list[str]:
    (sy, sm), (ey, em) = (map(int, s.split("-")) for s in (start, end))
    months = []
    y, m = sy, sm
    while (y, m) <= (ey, em):
        months.append(f"{y:04d}-{m:02d}")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return months


def select_month_databases(child_databases, start: str, end: str) -> dict[str, str]:
    """부모 아래 child database 전수 [(제목, id)…]에서 'get-ASAP YYYY-MM' 제목을 월별로 모아 {월: id}를 돌려준다.

    start~end 의 모든 월에 정확히 1개씩이어야 한다. 0개(누락)나 2개 이상(동명 DB - 어느 쪽이 정본인지
    알 수 없다)이면 조용히 하나를 고르지 않고 MigrationAbort(종료 코드 2)로 멈춘다.
    """
    wanted = _month_range(start, end)
    found: dict[str, list[str]] = defaultdict(list)
    for title, db_id in child_databases:
        match = _DB_TITLE_RE.match(title or "")
        if match and match.group(1) in wanted:
            found[match.group(1)].append(db_id)
    problems = [
        f"{month}: {len(found[month])}개" + (f" ({', '.join(found[month])})" if found[month] else "")
        for month in wanted
        if len(found[month]) != 1
    ]
    if problems:
        raise AmbiguousMonthDatabase(
            "월별 DB 'get-ASAP YYYY-MM' 이 월마다 정확히 1개여야 합니다 - " + "; ".join(problems)
            + ". Notion에서 동명 DB를 정리(또는 --start/--end 로 범위 조정)한 뒤 다시 실행하세요."
        )
    return {month: found[month][0] for month in wanted}


# ---------- Notion 접근 (테스트에서는 같은 메서드를 가진 가짜로 대체) ----------

class NotionSource:
    """실제 Notion REST 접근. month_databases / query / retrieve_page 세 메서드가 전부다."""

    def __init__(self):
        import config

        if not config.NOTION_TOKEN or not config.NOTION_PARENT_PAGE_ID:
            raise SystemExit("NOTION_TOKEN / NOTION_PARENT_PAGE_ID 환경변수가 필요합니다(.env).")
        self._parent = config.NOTION_PARENT_PAGE_ID
        self._headers = {
            "Authorization": f"Bearer {config.NOTION_TOKEN}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
        }

    def child_databases(self) -> list[tuple[str, str]]:
        """부모 페이지 바로 아래 child_database 전부를 (제목, id)로. notion_client_mod._find_monthly_db 는 첫 일치만
        돌려주므로(동명 DB가 있어도 모른다) 이관용으로는 전수 목록을 직접 받는다."""
        found: list[tuple[str, str]] = []
        cursor = None
        while True:
            params = {"page_size": 100}
            if cursor:
                params["start_cursor"] = cursor
            result = self._request("GET", NOTION_CHILDREN_URL.format(self._parent), params=params)
            for block in result.get("results", []):
                if block.get("type") == "child_database":
                    found.append(((block.get("child_database") or {}).get("title", ""), block["id"]))
            if not result.get("has_more"):
                return found
            cursor = result.get("next_cursor")

    def month_databases(self, start: str, end: str) -> dict[str, str]:
        return select_month_databases(self.child_databases(), start, end)

    def _request(self, method: str, url: str, **kwargs) -> dict:
        import httpx

        for attempt in range(6):
            try:
                resp = httpx.request(method, url, headers=self._headers, timeout=60, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as e:
                wait = 2 ** attempt
                logger.warning("Notion 네트워크 오류(%s), %d초 후 재시도", type(e).__name__, wait)
                time.sleep(wait)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code in (429, 500, 502, 503, 504):
                wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                logger.warning("Notion %d, %.0f초 후 재시도", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
        raise RuntimeError(f"Notion 요청 재시도 초과: {method} {url.split('?')[0]}")

    def query(self, database_id: str, payload: dict) -> dict:
        return self._request("POST", NOTION_QUERY_URL.format(database_id), json=payload)

    def retrieve_page(self, page_id: str) -> dict:
        return self._request("GET", NOTION_PAGE_URL.format(page_id))


# ---------- 페이지 -> 행 ----------

def _rich_text(prop_value: dict, key: str) -> tuple[str, int]:
    """rich_text/title 조각 전체를 이어 붙인 문자열과 조각 수."""
    parts = (prop_value or {}).get(key) or []
    return "".join(seg.get("plain_text", "") for seg in parts), len(parts)


def parse_page(page: dict) -> tuple[dict, dict]:
    """Notion page -> (행 dict[bucket_month 제외], 관찰 메모).

    메모: multi_title/multi_reason(조각이 여럿), status_empty, unknown_label(None 또는 라벨), date_issue(''/empty/with_time/invalid), empty_title.
    """
    props = page.get("properties", {})
    title, title_parts = _rich_text(props.get("Title"), "title")
    reason, reason_parts = _rich_text(props.get("GPT Reason"), "rich_text")
    journal_sel = (props.get("Journal") or {}).get("select")
    status_sel = (props.get("Status") or {}).get("select")
    date_obj = (props.get("Date") or {}).get("date")
    raw_date = (date_obj.get("start") if date_obj else "") or ""
    pub_date = store.normalize_pub_date(raw_date)
    if not raw_date:
        date_issue = "empty"
    elif pub_date is None:
        date_issue = "invalid"
    elif len(raw_date) > 10:
        date_issue = "with_time"
    else:
        date_issue = ""
    status = (status_sel or {}).get("name") or ""
    notes = {
        "multi_title": title_parts > 1,
        "multi_reason": reason_parts > 1,
        "status_empty": not status,
        "unknown_label": status if status and status not in store.STATUSES else None,
        "date_issue": date_issue,
        "empty_title": not title.strip(),
    }
    row = {
        "ref": str(page["id"]).lower(),
        "title": title,
        "journal": (journal_sel or {}).get("name") or "",
        "url": (props.get("URL") or {}).get("url") or "",
        "pub_date": pub_date,
        "status": status or store.DEFAULT_STATUS,
        "gpt_reason": reason,
        "created_at": page.get("created_time") or store._now(),
        "notion_last_edited": page.get("last_edited_time"),
    }
    return row, notes


# ---------- 분할 조회 ----------

def iter_filtered(source, database_id: str, flt: dict | None, cap: int | None = None):
    """필터 하나의 결과를 응답(최대 100건) 단위로 yield. 한 창이 cap(기본 WINDOW_CAP)건 이상이면 중단."""
    cap = cap or WINDOW_CAP
    cursor, total = None, 0
    while True:
        payload = {"page_size": 100}
        if flt:
            payload["filter"] = flt
        if cursor:
            payload["start_cursor"] = cursor
        resp = source.query(database_id, payload)
        results = resp.get("results", [])
        total += len(results)
        if total >= cap:
            raise MigrationAbort(f"필터 창 하나가 {total}건에 도달했습니다(10k 상한 근접) - 창을 더 잘게 나눠야 합니다: {flt}")
        if results:
            yield results
        if not resp.get("has_more"):
            return
        cursor = resp.get("next_cursor")


def _created_day(page: dict) -> date:
    ts = datetime.fromisoformat(page["created_time"].replace("Z", "+00:00"))
    return ts.astimezone(timezone.utc).date()


def _created_filter(day: date) -> dict:
    nxt = day + timedelta(days=1)
    return {"and": [
        {"timestamp": "created_time", "created_time": {"on_or_after": f"{day.isoformat()}T00:00:00.000Z"}},
        {"timestamp": "created_time", "created_time": {"before": f"{nxt.isoformat()}T00:00:00.000Z"}},
    ]}


def iter_partition_created(source, database_id: str):
    """분할 A: created_time 일 단위 창 (DB의 최초~최종 생성일 사이의 모든 날)."""
    def edge(direction: str):
        resp = source.query(database_id, {
            "page_size": 1, "sorts": [{"timestamp": "created_time", "direction": direction}],
        })
        results = resp.get("results", [])
        return _created_day(results[0]) if results else None

    first, last = edge("ascending"), edge("descending")
    if first is None or last is None:
        return
    day = first
    while day <= last:
        yield from iter_filtered(source, database_id, _created_filter(day))
        day += timedelta(days=1)


def iter_partition_date(source, database_id: str, month: str):
    """분할 B: 기존 Date 필터 (월 이전 + 주 단위 창 + 월 이후 + 빈 Date) - notion_fetcher._fetch_month_pages와 동일."""
    from analytics import notion_fetcher as nf

    ranges = nf._weekly_ranges(month)
    month_start, month_end = ranges[0][0], ranges[-1][1]
    filters = (
        [nf._date_before_filter(month_start)]
        + [nf._date_range_filter(s, e) for s, e in ranges]
        + [nf._date_on_or_after_filter(month_end), nf._empty_date_filter()]
    )
    for flt in filters:
        yield from iter_filtered(source, database_id, flt)


# ---------- 이관 본체 ----------

def _new_month_report(month: str, db_id: str) -> dict:
    return {
        "db_id": db_id, "count_a": 0, "count_b": 0, "sets_equal": None,
        "inserted": 0, "updated": 0, "unchanged": 0, "protected": 0, "db_rows": 0, "dup_rows": 0,
        "multi_title": 0, "multi_reason": 0, "status_empty": 0, "unknown_labels": 0, "empty_title": 0,
        "date_empty": 0, "date_with_time": 0, "date_invalid": 0,
        "multi_title_refs": [], "multi_reason_refs": [], "only_in_a": [], "only_in_b": [],
    }


def _apply_batch(conn, pages: list[dict], month: str, mr: dict) -> None:
    """페이지 한 응답(<=100건)을 파싱해 한 트랜잭션으로 upsert. 모르는 라벨이 있으면 쓰기 전에 중단한다."""
    parsed = [parse_page(p) for p in pages]
    unknown = sorted({n["unknown_label"] for _, n in parsed if n["unknown_label"]})
    if unknown:
        mr["unknown_labels"] += sum(1 for _, n in parsed if n["unknown_label"])
        raise UnknownStatusLabel(f"{month}: 모르는 Status 라벨 {unknown}")
    with store.write_tx(conn):
        now = store._now()
        for row, notes in parsed:
            row["bucket_month"] = month
            mr[store.upsert_migrated(conn, row, now)] += 1
            if notes["multi_title"]:
                mr["multi_title"] += 1
                if len(mr["multi_title_refs"]) < SAMPLE_LIMIT:
                    mr["multi_title_refs"].append(row["ref"])
            if notes["multi_reason"]:
                mr["multi_reason"] += 1
                if len(mr["multi_reason_refs"]) < SAMPLE_LIMIT:
                    mr["multi_reason_refs"].append(row["ref"])
            mr["status_empty"] += notes["status_empty"]
            mr["empty_title"] += notes["empty_title"]
            if notes["date_issue"]:
                mr["date_" + notes["date_issue"]] += 1


def migrate(conn, source, months: dict[str, str], report: dict, progress=print) -> dict:
    """월별 DB를 순서대로 이관한다. 진행 상황은 report에 바로 반영되므로 중단돼도 부분 보고서가 남는다."""
    report.setdefault("months", {})
    for month in sorted(months):
        mr = report["months"][month] = _new_month_report(month, months[month])
        ids_a: set[str] = set()
        for pages in iter_partition_created(source, months[month]):
            ids_a.update(str(p["id"]).lower() for p in pages)
            _apply_batch(conn, pages, month, mr)
        ids_b: set[str] = set()
        for pages in iter_partition_date(source, months[month], month):
            ids_b.update(str(p["id"]).lower() for p in pages)
        mr["count_a"], mr["count_b"], mr["sets_equal"] = len(ids_a), len(ids_b), ids_a == ids_b
        mr["db_rows"] = conn.execute("SELECT COUNT(*) FROM papers WHERE bucket_month=?", (month,)).fetchone()[0]
        mr["dup_rows"] = conn.execute(
            "SELECT COUNT(*) FROM papers WHERE bucket_month=? AND dup_of IS NOT NULL", (month,)
        ).fetchone()[0]
        progress(
            f"[{month}] A={mr['count_a']} B={mr['count_b']} 일치={mr['sets_equal']} "
            f"신규={mr['inserted']} 갱신={mr['updated']} 동일={mr['unchanged']} 보호={mr['protected']} "
            f"중복행={mr['dup_rows']} 제목조각多={mr['multi_title']} 사유조각多={mr['multi_reason']} "
            f"상태빈값={mr['status_empty']} 날짜(빈값/시각포함/비정상)={mr['date_empty']}/{mr['date_with_time']}/{mr['date_invalid']}"
        )
        if not mr["sets_equal"]:
            mr["only_in_a"] = sorted(ids_a - ids_b)[:SAMPLE_LIMIT]
            mr["only_in_b"] = sorted(ids_b - ids_a)[:SAMPLE_LIMIT]
            raise PartitionMismatch(
                f"{month}: 두 분할의 page-id 집합이 다릅니다 (A만 {len(ids_a - ids_b)}건, B만 {len(ids_b - ids_a)}건)"
            )
        with store.write_tx(conn):
            conn.execute(
                "INSERT INTO meta(key, value) VALUES(?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (f"migrated:{month}", store._now()),
            )
    return report


# ---------- Gate 3: CSV multiset 비교 ----------

def read_csv(path: str) -> tuple[list[str], list[tuple]] | None:
    """(헤더, 행 튜플 목록). 파일이 없으면 None."""
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = [tuple(r) for r in csv.reader(f) if r]
    return (list(rows[0]), rows[1:]) if rows else ([], [])


def classify_diff(only_csv: list[tuple], only_db: list[tuple]) -> dict:
    """multiset 차이를 유형별로 분류. 행 튜플 순서: title, journal, date, url, status, gpt_reason.

    같은 (title,url)이면서 필드만 다른 쌍 -> changed:<필드들> / url이 같고 CSV 제목이 DB 제목의 앞부분 ->
    changed:title_truncated(Notion title 조각이 여럿이면 옛 파서는 첫 조각만 읽었다) / 제목만 같음 -> changed:url...
    짝이 없으면 only_csv(옛 CSV에만) / only_db(DB에만).
    """
    used = [False] * len(only_db)
    by_title_url, by_url, by_title = defaultdict(list), defaultdict(list), defaultdict(list)
    for i, d in enumerate(only_db):
        by_title_url[(d[0], d[3])].append(i)
        if d[3]:
            by_url[d[3]].append(i)
        by_title[d[0]].append(i)

    def take(indexes, accept=lambda d: True):
        for i in indexes:
            if not used[i] and accept(only_db[i]):
                used[i] = True
                return i
        return None

    classes, samples = Counter(), defaultdict(list)
    only_csv_left = []
    for c in only_csv:
        kind, idx = "", take(by_title_url.get((c[0], c[3]), ()))
        if idx is None and c[3]:
            idx = take(by_url.get(c[3], ()), lambda d: d[0].startswith(c[0]) and d[0] != c[0])
            kind = "title_truncated" if idx is not None else ""
        if idx is None:
            idx = take(by_title.get(c[0], ()))
            kind = "url" if idx is not None else ""
        if idx is None:
            only_csv_left.append(c)
            continue
        d = only_db[idx]
        fields = [name for name, i in (("journal", 1), ("date", 2), ("status", 4), ("gpt_reason", 5)) if c[i] != d[i]]
        label = "changed:" + "+".join(([kind] if kind else []) + fields)
        classes[label] += 1
        if len(samples[label]) < 3:
            samples[label].append({"csv": list(c), "db": list(d)})
    only_db_left = [only_db[i] for i, u in enumerate(used) if not u]
    if only_csv_left:
        classes["only_csv"] = len(only_csv_left)
        samples["only_csv"] = [{"csv": list(r)} for r in only_csv_left[:3]]
    if only_db_left:
        classes["only_db"] = len(only_db_left)
        samples["only_db"] = [{"db": list(r)} for r in only_db_left[:3]]
    return {"classes": dict(classes), "samples": dict(samples)}


def compare_cache(conn, cache_dir: str, today: date | None = None) -> dict:
    """SQLite에서 export한 CSV(임시 폴더)와 기존 cache/*.csv를 월별 행 multiset으로 비교한다.

    통과 조건: 이번 달·지난달은 차이 0 / 그 이전 달은 only_csv(옛 CSV에만 있는 행 = 이관 누락 의심)가 0.
    그 외 유형(changed:*, only_db)은 설명 가능한 차이로 보고 유형별로 표기한다.
    """
    today = today or date.today()
    this_month = today.strftime("%Y-%m")
    prev_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    strict = {this_month, prev_month}
    db_months = {r[0] for r in conn.execute("SELECT DISTINCT bucket_month FROM papers")}
    csv_months = {
        n[len("papers_"):-len(".csv")] for n in (os.listdir(cache_dir) if os.path.isdir(cache_dir) else [])
        if n.startswith("papers_") and n.endswith(".csv")
    }
    result = {"months": {}, "ok": bool(csv_months), "note": "" if csv_months else f"{cache_dir} 에 papers_*.csv 가 없습니다."}
    with tempfile.TemporaryDirectory(prefix="asap_gate3_") as tmp:
        for month in sorted(db_months | csv_months):
            if month in db_months:
                store.export_csv(month, conn, tmp, track=False)
            old = read_csv(store.csv_path(month, cache_dir))
            new = read_csv(store.csv_path(month, tmp))
            old_rows = Counter(old[1] if old else [])
            new_rows = Counter(new[1] if new else [])
            only_csv = list((old_rows - new_rows).elements())
            only_db = list((new_rows - old_rows).elements())
            diff = classify_diff(only_csv, only_db)
            header_ok = (old is None or new is None or old[0] == new[0])
            month_ok = header_ok and (
                not (only_csv or only_db) if month in strict else "only_csv" not in diff["classes"]
            )
            result["months"][month] = {
                "strict": month in strict, "csv_rows": sum(old_rows.values()), "db_rows": sum(new_rows.values()),
                "diff_total": len(only_csv) + len(only_db), "header_ok": header_ok, "ok": month_ok, **diff,
            }
            result["ok"] = result["ok"] and month_ok
    return result


# ---------- Gate 5: 무작위 행 vs Notion ----------

def sample_compare(conn, source, n: int, rng: random.Random) -> dict:
    refs = [r[0] for r in conn.execute("SELECT ref FROM papers WHERE status_source='notion_migrated'")]
    picked = rng.sample(refs, min(n, len(refs)))
    mismatches = []
    for ref in picked:
        row = conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()
        try:
            fresh, _ = parse_page(source.retrieve_page(ref))
        except Exception as e:  # noqa: BLE001 - 조회 실패도 불일치로 센다
            mismatches.append({"ref": ref, "error": str(e)[:200]})
            continue
        bad = {k: {"db": row[k], "notion": fresh[k]}
               for k in ("title", "journal", "url", "pub_date", "status", "gpt_reason", "created_at")
               if row[k] != fresh[k]}
        if bad:
            mismatches.append({"ref": ref, "fields": bad})
    return {"requested": n, "checked": len(picked), "mismatches": mismatches, "ok": bool(picked) and not mismatches}


# ---------- Gate 점검 / 보고 ----------

def run_gates(conn, report: dict, cache_dir: str, source=None, sample: int = 20,
              rng: random.Random | None = None, today: date | None = None) -> list[dict]:
    """§3의 5개 gate를 점검해 [{gate, name, ok, detail}]을 반환한다. source가 None이면 gate 5는 생략(ok=False)."""
    months = report.get("months", {})
    gates = []

    bad = [m for m, r in months.items() if r.get("sets_equal") is not True]
    gates.append({"gate": 1, "name": "두 분할의 page-id 집합 일치", "ok": bool(months) and not bad,
                  "detail": f"월 {len(months)}개" + (f", 불일치/미확인: {bad}" if bad else "")})

    count_bad = {}
    for m, r in months.items():
        have = conn.execute("SELECT COUNT(*) FROM papers WHERE bucket_month=?", (m,)).fetchone()[0]
        if have != r.get("count_a"):
            count_bad[m] = {"notion": r.get("count_a"), "sqlite": have}
    stray = [r[0] for r in conn.execute(
        "SELECT DISTINCT bucket_month FROM papers WHERE status_source='notion_migrated'") if r[0] not in months]
    gates.append({"gate": 2, "name": "bucket별 건수 = Notion 건수", "ok": bool(months) and not count_bad and not stray,
                  "detail": f"불일치: {count_bad}" if count_bad else (f"보고서에 없는 이관 bucket: {stray}" if stray else "모두 일치")})

    cmp_ = compare_cache(conn, cache_dir, today)
    gates.append({"gate": 3, "name": "CSV multiset (이번 달·지난달 동일, 이전 달 유형별 설명)", "ok": cmp_["ok"],
                  "detail": cmp_["note"] or {m: (v["diff_total"], v["classes"]) for m, v in cmp_["months"].items() if v["diff_total"] or not v["ok"]} or "차이 없음",
                  "data": cmp_})

    unknown = sum(r.get("unknown_labels", 0) for r in months.values())
    gates.append({"gate": 4, "name": "모르는 Status 라벨 0건", "ok": bool(months) and unknown == 0, "detail": f"{unknown}건"})

    if source is None:
        gates.append({"gate": 5, "name": "무작위 행 vs Notion", "ok": False, "detail": "생략됨(Notion 접근 없음) - 별도로 확인 필요"})
    else:
        samp = sample_compare(conn, source, sample, rng or random.Random())
        gates.append({"gate": 5, "name": f"무작위 {sample}행 vs Notion", "ok": samp["ok"],
                      "detail": f"{samp['checked']}행 확인, 불일치 {len(samp['mismatches'])}건", "data": samp})
    return gates


def print_gates(gates: list[dict], out=print) -> None:
    for g in gates:
        out(f"GATE {g['gate']} {'PASS' if g['ok'] else 'FAIL'}  {g['name']}: {g['detail']}")


def _json_default(o):
    return list(o) if isinstance(o, (set, tuple)) else str(o)


def write_report(path: str, report: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, default=_json_default)
    os.replace(tmp, path)


def print_summary(report: dict, out=print) -> None:
    months = report.get("months", {})
    keys = ("count_a", "inserted", "updated", "unchanged", "protected", "dup_rows", "multi_title", "multi_reason", "status_empty")
    totals = {k: sum(m.get(k, 0) for m in months.values()) for k in keys}
    out("합계: " + ", ".join(f"{k}={v}" for k, v in totals.items()))


# ---------- CLI ----------

def _default_months(start: str, end: str | None) -> tuple[str, str]:
    return start, end or date.today().strftime("%Y-%m")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Notion 월별 DB -> 정본 SQLite 일회성 이관 (설계 §3)")
    ap.add_argument("--dry-run", action="store_true", help="임시 DB에 쓰고 보고서·gate만 낸다(운영 DB·cache 불변)")
    ap.add_argument("--verify", action="store_true", help="본 실행 결과를 §3 gate 1~5로 점검")
    ap.add_argument("--db", help="DB 경로 (기본 ASAP_DB_PATH / data/asap.db). --dry-run에서는 무시")
    ap.add_argument("--report", help="보고서 JSON 경로 (기본: DB 옆 migration_report.json, dry-run은 임시 폴더)")
    ap.add_argument("--cache-dir", help="gate 3이 비교할 기존 CSV 폴더 (기본 cache/)")
    ap.add_argument("--start", default=os.getenv("REFRESH_ALL_START_MONTH", "2026-04"), help="시작 월 (기본 2026-04)")
    ap.add_argument("--end", help="종료 월 (기본 이번 달)")
    ap.add_argument("--sample", type=int, default=20, help="gate 5 표본 행 수")
    ap.add_argument("--seed", type=int, help="gate 5 표본 난수 시드")
    ap.add_argument("--skip-notion-sample", action="store_true", help="gate 5 생략(Notion 접근 없는 환경)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    if args.dry_run and args.verify:
        ap.error("--dry-run 과 --verify 는 함께 쓸 수 없습니다.")

    cache_dir = args.cache_dir or store.CACHE_DIR
    rng = random.Random(args.seed)
    tmp_dir = tempfile.mkdtemp(prefix="asap_migrate_dry_") if args.dry_run else None
    db_file = os.path.join(tmp_dir, "asap.db") if args.dry_run else (args.db or store.db_path())
    report_path = args.report or os.path.join(tmp_dir or os.path.dirname(os.path.abspath(db_file)), "migration_report.json")

    try:
        if args.verify:
            conn = store.connect(db_file)
            try:
                with open(report_path, encoding="utf-8") as f:
                    report = json.load(f)
                source = None if args.skip_notion_sample else NotionSource()
                gates = run_gates(conn, report, cache_dir, source, args.sample, rng)
            finally:
                conn.close()
            print_gates(gates)
            return 0 if all(g["ok"] for g in gates) else 1

        source = NotionSource()
        start, end = _default_months(args.start, args.end)
        try:
            months = source.month_databases(start, end)
        except MigrationAbort as e:  # DB를 만들기 전에 멈춘다(운영 DB·cache 불변)
            print(f"중단: {e}", file=sys.stderr)
            return 2
        if not months:
            print(f"{start}~{end} 범위에 월별 DB가 없습니다.", file=sys.stderr)
            return 3
        conn = store.connect(db_file, create=True)
        report = {
            "mode": "dry-run" if args.dry_run else "migrate", "db_path": db_file, "range": [start, end],
            "started": store._now(), "months": {},
        }
        try:
            store.init_schema(conn)
            print(f"이관 대상 {len(months)}개월: {', '.join(sorted(months))}  ->  {db_file}")
            try:
                migrate(conn, source, months, report)
            except MigrationAbort as e:
                report["aborted"] = str(e)
                write_report(report_path, report)
                print(f"중단: {e}\n보고서: {report_path}", file=sys.stderr)
                return 2
            report["finished"] = store._now()
            print_summary(report)
            gates = None
            if args.dry_run:
                gates = run_gates(conn, report, cache_dir, None if args.skip_notion_sample else source, args.sample, rng)
                report["gates"] = [{k: v for k, v in g.items() if k != "data"} for g in gates]
            write_report(report_path, report)
        finally:
            conn.close()
        print(f"보고서: {report_path}")
        if args.dry_run:
            print_gates(gates)
            print(f"(dry-run 임시 DB: {db_file})")
            return 0 if all(g["ok"] for g in gates) else 1
        print("다음: python migrate_from_notion.py --verify")
        return 0
    except Exception:
        logger.exception("이관 실패")
        return 3


if __name__ == "__main__":
    sys.exit(main())
