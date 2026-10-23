"""migrate_from_notion.py 회귀 테스트 - Notion에는 닿지 않고 합성 페이지를 돌려주는 가짜 소스로 검증한다.

가짜 소스는 이관 스크립트가 쓰는 두 종류의 필터(created_time 일 창, Date 주 창/범위 전·후/빈 값)와
페이지네이션, 정렬 조회를 실제 Notion처럼 평가한다.
"""
import copy
import json
import os
import random
from datetime import date

import pytest

import migrate_from_notion as mig
import store


# ---------- 가짜 Notion ----------

def make_page(n, title, *, created, date_start="2026-09-05", status="대기중", journal="Synthetic J", url=None,
              reason="", title_parts=None, reason_parts=None):
    pid = f"{n:08x}-0000-4000-8000-{n:012x}"
    title_obj = [{"plain_text": p} for p in (title_parts or [title])]
    reason_obj = [{"plain_text": p} for p in (reason_parts or ([reason] if reason else []))]
    return {
        "id": pid, "created_time": created, "last_edited_time": created,
        "properties": {
            "Title": {"title": title_obj},
            "Journal": {"select": {"name": journal} if journal else None},
            "Date": {"date": {"start": date_start} if date_start else None},
            "URL": {"url": url if url is not None else f"https://example.invalid/{n}"},
            "Status": {"select": {"name": status} if status else None},
            "GPT Reason": {"rich_text": reason_obj},
        },
    }


class FakeNotion:
    """months: {"2026-09": [page, ...]}. page_cap은 한 응답의 최대 건수(페이지네이션 검증용으로 작게)."""

    def __init__(self, months, page_cap=3):
        self.dbs = {f"db-{m}": pages for m, pages in months.items()}
        self.months = list(months)
        self.page_cap = page_cap
        self.payloads = []                      # 받은 질의 기록(어떤 필터를 썼는지 검증용)
        self.hide_from_date_partition = set()   # Date 분할에서만 누락시킬 page id
        self.retrieve_overrides = {}

    def month_databases(self, start, end):
        return {m: f"db-{m}" for m in self.months if start <= m <= end}

    def _date_start(self, page):
        d = page["properties"]["Date"]["date"]
        return d["start"] if d else None

    def _match(self, page, flt):
        if flt is None:
            return True
        if "and" in flt:
            return all(self._match(page, f) for f in flt["and"])
        if flt.get("timestamp") == "created_time":
            cond = flt["created_time"]
            ts = page["created_time"]
            return all(
                (ts >= v if op == "on_or_after" else ts < v) for op, v in cond.items()
            )
        assert flt["property"] == "Date"
        cond, start = flt["date"], self._date_start(page)
        if "is_empty" in cond:
            return start is None
        if start is None:
            return False
        d = start[:10]
        return all((d >= v if op == "on_or_after" else d < v) for op, v in cond.items())

    def query(self, database_id, payload):
        self.payloads.append(copy.deepcopy(payload))
        pages = [p for p in self.dbs[database_id] if self._match(p, payload.get("filter"))]
        if '"property": "Date"' in json.dumps(payload.get("filter") or {}):
            pages = [p for p in pages if p["id"] not in self.hide_from_date_partition]
        for sort in payload.get("sorts", []):
            pages.sort(key=lambda p: p["created_time"], reverse=sort["direction"] == "descending")
        start = int(payload.get("start_cursor") or 0)
        size = min(payload.get("page_size", 100), self.page_cap)
        chunk = pages[start:start + size]
        more = start + size < len(pages)
        return {"results": copy.deepcopy(chunk), "has_more": more, "next_cursor": str(start + size) if more else None}

    def retrieve_page(self, page_id):
        if page_id in self.retrieve_overrides:
            return self.retrieve_overrides[page_id]
        for pages in self.dbs.values():
            for p in pages:
                if p["id"] == page_id:
                    return copy.deepcopy(p)
        raise KeyError(page_id)


def sample_months():
    """2개월 / 다양한 모양: 월 밖 Date, 빈 Date, 시각 포함 Date, 조각 여러 개, 빈 상태, 대소문자 중복 제목."""
    aug = [
        make_page(1, "August paper one", created="2026-08-03T01:00:00.000Z", date_start="2026-08-03", status="관련", reason="r1"),
        make_page(2, "August paper two", created="2026-08-03T02:00:00.000Z", date_start="2026-08-04", status="다운완료"),
        make_page(3, "Late-arriving July paper", created="2026-08-10T00:00:00.000Z", date_start="2026-07-30"),  # 월 밖(이전)
        make_page(4, "No date paper", created="2026-08-12T00:00:00.000Z", date_start=None),
    ]
    sep = [
        make_page(10, "September one", created="2026-09-01T01:00:00.000Z", date_start="2026-09-01"),
        make_page(11, "September two", created="2026-09-02T01:00:00.000Z", date_start="2026-09-02", status=None),  # 빈 상태 -> 대기중
        make_page(12, "Timed date", created="2026-09-02T02:00:00.000Z", date_start="2026-09-02T10:30:00.000+09:00"),
        make_page(13, "ignored", created="2026-09-03T01:00:00.000Z", date_start="2026-10-02",  # 월 밖(이후)
                  title_parts=["Split title part A, ", "part B"], reason_parts=["why: ", "because"], status="불확실"),
        make_page(14, "Duplicate Title", created="2026-09-04T01:00:00.000Z", date_start="2026-09-04"),
        make_page(15, "duplicate  TITLE", created="2026-09-05T01:00:00.000Z", date_start="2026-09-05"),  # 이관 전부터 있던 중복
        make_page(16, "Wide day A", created="2026-09-06T23:59:00.000Z", date_start="2026-09-06"),
        make_page(17, "Wide day B", created="2026-09-07T00:00:00.000Z", date_start="2026-09-07"),
    ]
    return {"2026-08": aug, "2026-09": sep}


@pytest.fixture
def fake():
    return FakeNotion(sample_months())


@pytest.fixture
def work(tmp_path):
    """이관 대상 DB(conn)와 보고서 dict."""
    db = tmp_path / "mig" / "asap.db"
    c = store.connect(str(db), create=True)
    store.init_schema(c)
    yield c, {"months": {}}
    c.close()


def run(conn, source, report):
    return mig.migrate(conn, source, source.month_databases("2026-04", "2026-12"), report, progress=lambda *_: None)


def all_rows(conn):
    return {r["title"]: r for r in conn.execute("SELECT * FROM papers")}


# ---------- 이관 ----------

def test_full_migration_field_rules(work, fake):
    conn, report = work
    run(conn, fake, report)
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 12
    rows = all_rows(conn)

    one = rows["August paper one"]
    assert one["ref"] == "00000001-0000-4000-8000-000000000001" and one["status"] == "관련" and one["gpt_reason"] == "r1"
    assert one["status_source"] == "notion_migrated" and one["bucket_month"] == "2026-08"
    assert one["created_at"] == "2026-08-03T01:00:00.000Z" and one["pub_date"] == "2026-08-03"
    # bucket = 원래 DB의 월 (Date가 월 밖/비어 있어도)
    assert rows["Late-arriving July paper"]["bucket_month"] == "2026-08" and rows["Late-arriving July paper"]["pub_date"] == "2026-07-30"
    assert rows["No date paper"]["bucket_month"] == "2026-08" and rows["No date paper"]["pub_date"] is None
    # 조각은 전부 이어 붙인다(제목·사유)
    split = rows["Split title part A, part B"]
    assert split["gpt_reason"] == "why: because" and split["bucket_month"] == "2026-09" and split["pub_date"] == "2026-10-02"
    # 빈 상태는 대기중, 시각 포함 날짜는 날짜만
    assert rows["September two"]["status"] == "대기중" and rows["Timed date"]["pub_date"] == "2026-09-02"
    # 중복은 모두 보존하고 두 번째 이후에 dup_of
    dups = [r for r in conn.execute("SELECT * FROM papers WHERE title_key='duplicate title'")]
    assert len(dups) == 2 and sorted(r["dup_of"] is None for r in dups) == [False, True]
    first = next(r for r in dups if r["dup_of"] is None)
    assert next(r for r in dups if r["dup_of"] is not None)["dup_of"] == first["id"]
    # 모든 행이 변경 피드에 올라간다
    assert store.fetch_changes(conn, 0, 100)["last_seq"] == 12

    sep = report["months"]["2026-09"]
    assert (sep["count_a"], sep["count_b"], sep["sets_equal"], sep["db_rows"], sep["dup_rows"]) == (8, 8, True, 8, 1)
    assert (sep["inserted"], sep["multi_title"], sep["multi_reason"], sep["status_empty"]) == (8, 1, 1, 1)
    assert (sep["date_with_time"], sep["date_empty"], sep["unknown_labels"]) == (1, 0, 0)
    assert report["months"]["2026-08"]["date_empty"] == 1
    assert report["months"]["2026-08"]["multi_title_refs"] == []
    assert conn.execute("SELECT key FROM meta WHERE key LIKE 'migrated:%' ORDER BY key").fetchall()[0][0] == "migrated:2026-08"


def test_partitions_use_independent_filters_and_paginate(work, fake):
    conn, report = work
    run(conn, fake, report)
    texts = [json.dumps(p.get("filter") or {}) for p in fake.payloads]
    assert any('"timestamp": "created_time"' in t for t in texts)                      # 분할 A: created_time 일 창
    assert any('"property": "Date"' in t and "is_empty" in t for t in texts)           # 분할 B: 빈 Date
    assert any('"property": "Date"' in t and '"before"' in t and '"and"' not in t for t in texts)  # 월 이전
    assert any('"property": "Date"' in t and '"on_or_after"' in t and '"and"' not in t for t in texts)  # 월 이후
    assert sum('"and"' in t and "Date" in t for t in texts) >= 8                       # 두 달 x 주 단위 창
    assert any(p.get("start_cursor") for p in fake.payloads)                           # 페이지네이션
    assert any(p.get("sorts") for p in fake.payloads)                                  # 최초·최종 생성일 조회


def test_rerun_is_idempotent(work, fake):
    conn, report = work
    run(conn, fake, report)
    snapshot = [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")]
    seq = store.last_seq(conn)
    report2 = {"months": {}}
    run(conn, fake, report2)
    assert [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")] == snapshot
    assert store.last_seq(conn) == seq
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 0
    for mr in report2["months"].values():
        assert mr["inserted"] == mr["updated"] == mr["protected"] == 0 and mr["unchanged"] == mr["db_rows"]


def test_rerun_picks_up_notion_edits_but_never_overwrites_other_sources(work, fake):
    conn, report = work
    run(conn, fake, report)
    seq = store.last_seq(conn)
    # Notion에서 한 행의 상태·사유를 고치고, 다른 행은 이관 후 paper_autodown이 판정했다
    fake.dbs["db-2026-08"][0]["properties"]["Status"] = {"select": {"name": "무관"}}
    fake.dbs["db-2026-08"][0]["properties"]["GPT Reason"] = {"rich_text": [{"plain_text": "edited in notion"}]}
    protected_ref = fake.dbs["db-2026-08"][1]["id"]
    store.apply_verdicts(conn, [{"ref": protected_ref, "status": "불확실"}], 0)
    fake.dbs["db-2026-08"][1]["properties"]["Status"] = {"select": {"name": "관련"}}
    report2 = {"months": {}}
    run(conn, fake, report2)
    mr = report2["months"]["2026-08"]
    assert (mr["updated"], mr["protected"], mr["inserted"]) == (1, 1, 0)
    rows = all_rows(conn)
    assert (rows["August paper one"]["status"], rows["August paper one"]["gpt_reason"]) == ("무관", "edited in notion")
    assert (rows["August paper two"]["status"], rows["August paper two"]["status_source"]) == ("불확실", "paper_autodown")
    assert store.last_seq(conn) == seq + 2  # Notion 수정 1건 + 판정 1건
    hist = conn.execute("SELECT new_status, new_source FROM status_history ORDER BY id").fetchall()
    assert [tuple(h) for h in hist] == [("불확실", "paper_autodown"), ("무관", "notion_migrated")]


def test_partition_mismatch_aborts_with_diagnostics(work, fake):
    conn, report = work
    hidden = fake.dbs["db-2026-09"][3]["id"]  # Date 분할에서만 안 보이는 페이지(조회 누락 시뮬레이션)
    fake.hide_from_date_partition.add(hidden)
    with pytest.raises(mig.PartitionMismatch, match="2026-09"):
        run(conn, fake, report)
    sep = report["months"]["2026-09"]
    assert sep["sets_equal"] is False and sep["count_a"] == 8 and sep["count_b"] == 7 and sep["only_in_a"] == [hidden]
    assert report["months"]["2026-08"]["sets_equal"] is True
    meta = [r[0] for r in conn.execute("SELECT key FROM meta WHERE key LIKE 'migrated:%'")]
    assert meta == ["migrated:2026-08"]  # 불일치한 달은 완료 표시를 남기지 않는다
    assert mig.run_gates(conn, report, "no-such-cache-dir", None)[0]["ok"] is False  # gate 1 실패


def test_window_cap_aborts_instead_of_silently_truncating(work, fake, monkeypatch):
    conn, report = work
    monkeypatch.setattr(mig, "WINDOW_CAP", 2)
    with pytest.raises(mig.MigrationAbort, match="10k"):
        run(conn, fake, report)


def test_unknown_status_label_aborts_before_any_write_of_that_batch(work, fake):
    conn, report = work
    # 9/2에 만들어진 두 페이지(11, 12)가 같은 일 창 배치다. 그중 하나가 모르는 라벨이면 그 배치는 아무것도 쓰이지 않는다.
    fake.dbs["db-2026-09"][2]["properties"]["Status"] = {"select": {"name": "Archived?"}}
    fake.page_cap = 100
    with pytest.raises(mig.UnknownStatusLabel, match="Archived"):
        run(conn, fake, report)
    assert report["months"]["2026-09"]["unknown_labels"] == 1
    titles = {r[0] for r in conn.execute("SELECT title FROM papers WHERE bucket_month='2026-09'")}
    assert titles == {"September one"}  # 그 앞 날짜 배치는 쓰였고, 문제 배치(September two + Timed date)는 쓰이지 않았다
    assert mig.run_gates(conn, report, "x", None)[3]["ok"] is False  # gate 4 실패


def test_empty_database_month_is_fine(work):
    conn, report = work
    run(conn, FakeNotion({"2026-09": []}), report)
    assert report["months"]["2026-09"]["sets_equal"] is True and report["months"]["2026-09"]["count_a"] == 0


def test_parse_page_notes():
    row, notes = mig.parse_page(make_page(5, "x", created="2026-09-01T00:00:00.000Z", date_start="garbage", status=None, journal=None, title_parts=["", ""]))
    assert notes["date_issue"] == "invalid" and row["pub_date"] is None and row["status"] == "대기중" and row["journal"] == ""
    assert notes["status_empty"] and notes["empty_title"] and notes["multi_title"]


# ---------- Gate ----------

def migrated_env(tmp_path, fake):
    db = tmp_path / "gate.db"
    conn = store.connect(str(db), create=True)
    store.init_schema(conn)
    report = {"months": {}}
    run(conn, fake, report)
    cache = tmp_path / "gate_cache"
    store.export_all(conn, str(cache))
    return conn, report, str(cache)


TODAY = date(2026, 9, 20)  # 이번 달=2026-09, 지난달=2026-08 (엄격 비교 대상)


def test_all_gates_pass_when_cache_matches_and_notion_sample_agrees(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    gates = mig.run_gates(conn, report, cache, fake, sample=20, rng=random.Random(1), today=TODAY)
    assert [(g["gate"], g["ok"]) for g in gates] == [(i, True) for i in range(1, 6)], gates
    assert gates[4]["data"]["checked"] == 12  # 행이 20개보다 적으면 전부 확인
    conn.close()


def test_gate2_detects_count_mismatch(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    conn.execute("DELETE FROM papers WHERE title='September one'")
    gates = mig.run_gates(conn, report, cache, fake, today=TODAY)
    assert gates[1]["ok"] is False and "2026-09" in str(gates[1]["detail"])
    conn.close()


def test_gate3_strict_for_recent_months_classified_for_older(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    sep_csv, aug_csv = store.csv_path("2026-09", cache), store.csv_path("2026-08", cache)

    def edit(path, fn):
        text = open(path, encoding="utf-8-sig", newline="").read()
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            f.write(fn(text))

    # 지난달(2026-08, 엄격): 옛 CSV와 status 하나만 달라도 실패
    edit(aug_csv, lambda t: t.replace('"August paper one","Synthetic J","2026-08-03","https://example.invalid/1","관련"',
                                      '"August paper one","Synthetic J","2026-08-03","https://example.invalid/1","대기중"'))
    res = mig.compare_cache(conn, cache, TODAY)
    assert res["months"]["2026-08"]["ok"] is False and res["months"]["2026-08"]["classes"] == {"changed:status": 1}
    assert res["ok"] is False

    # 같은 차이라도 오래된 달(today를 뒤로 밀어 2026-08을 비엄격으로)이면 설명 가능한 유형으로 분류되어 통과
    res = mig.compare_cache(conn, cache, date(2026, 11, 20))
    assert res["months"]["2026-08"]["strict"] is False and res["months"]["2026-08"]["ok"] is True
    assert res["months"]["2026-08"]["classes"] == {"changed:status": 1}
    assert res["months"]["2026-09"]["ok"] is True  # 2026-09도 이제 비엄격이고 동일

    # 옛 파서는 title 첫 조각만 읽었다 -> title_truncated, 사유 변경 / 옛 CSV 이후 추가된 행(only_db)
    edit(sep_csv, lambda t: t.replace('"Split title part A, part B"', '"Split title part A, "')
                              .replace('"why: because"', '"why:"')
                              .replace('"Wide day A"', '"Wide day A!"'))
    classes = mig.compare_cache(conn, cache, date(2026, 11, 20))["months"]["2026-09"]["classes"]
    assert classes == {"changed:title_truncated+gpt_reason": 1, "only_csv": 1, "only_db": 1}
    conn.close()


def test_gate3_only_csv_rows_in_old_months_fail(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    path = store.csv_path("2026-08", cache)
    with open(path, "a", encoding="utf-8-sig", newline="") as f:
        f.write('"Row lost in migration","J","2026-08-09","https://example.invalid/lost","관련",""\r\n')
    res = mig.compare_cache(conn, cache, date(2026, 11, 20))
    assert res["months"]["2026-08"]["classes"] == {"only_csv": 1} and res["months"]["2026-08"]["ok"] is False and res["ok"] is False
    conn.close()


def test_gate3_reports_missing_cache(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    res = mig.compare_cache(conn, str(tmp_path / "empty"), TODAY)
    assert res["ok"] is False and "papers_*.csv" in res["note"]
    conn.close()


def test_gate3_does_not_touch_real_cache_or_csv_seq(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    before = {n: os.stat(os.path.join(cache, n)).st_mtime_ns for n in os.listdir(cache)}
    meta_before = conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
    mig.compare_cache(conn, cache, TODAY)
    assert {n: os.stat(os.path.join(cache, n)).st_mtime_ns for n in os.listdir(cache)} == before
    assert conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall() == meta_before
    conn.close()


def test_gate5_detects_field_mismatch_with_notion(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    victim = fake.dbs["db-2026-09"][0]
    victim["properties"]["Journal"] = {"select": {"name": "Changed After Migration"}}
    res = mig.sample_compare(conn, fake, 50, random.Random(3))
    assert res["ok"] is False and len(res["mismatches"]) == 1
    assert res["mismatches"][0]["fields"]["journal"]["notion"] == "Changed After Migration"
    victim_ref = victim["id"]
    del fake.dbs["db-2026-09"][0]  # 조회 실패도 불일치로 센다
    res = mig.sample_compare(conn, fake, 50, random.Random(3))
    assert any(m["ref"] == victim_ref and "error" in m for m in res["mismatches"])
    conn.close()


def test_gate_without_notion_source_never_passes_gate5(tmp_path, fake):
    conn, report, cache = migrated_env(tmp_path, fake)
    gates = mig.run_gates(conn, report, cache, None, today=TODAY)
    assert [g["ok"] for g in gates] == [True, True, True, True, False]
    conn.close()


# ---------- classify_diff ----------

def test_classify_diff_pairing():
    csv_rows = [
        ("T1", "J", "2026-01-01", "u1", "대기중", ""),
        ("Trunc", "J", "2026-01-02", "u2", "관련", ""),
        ("T3", "J", "2026-01-03", "u3", "관련", "old"),
        ("Gone", "J", "2026-01-04", "u4", "관련", ""),
        ("T5", "J", "2026-01-05", "old-url", "관련", ""),
    ]
    db_rows = [
        ("T1", "J", "2026-01-01", "u1", "관련", ""),
        ("Trunc and more", "J", "2026-01-02", "u2", "관련", ""),
        ("T3", "J", "2026-01-03", "u3", "관련", "new"),
        ("Brand new", "J", "2026-01-06", "u6", "대기중", ""),
        ("T5", "J", "2026-01-05", "new-url", "관련", ""),
    ]
    res = mig.classify_diff(csv_rows, db_rows)["classes"]
    assert res == {"changed:status": 1, "changed:title_truncated": 1, "changed:gpt_reason": 1, "changed:url": 1,
                   "only_csv": 1, "only_db": 1}


# ---------- CLI ----------

def test_cli_dry_run_leaves_real_db_and_cache_untouched_then_real_run_and_verify(tmp_path, monkeypatch, fake, capsys):
    real_db = tmp_path / "real" / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(real_db))
    monkeypatch.setattr(mig, "NotionSource", lambda: fake)
    cache = tmp_path / "cache"

    # 리허설: 기존 cache가 아직 없으면 gate 3이 실패하지만 DB·cache는 건드리지 않는다
    rc = mig.main(["--dry-run", "--cache-dir", str(cache), "--seed", "1"])
    out = capsys.readouterr().out
    assert rc == 1 and "GATE 3 FAIL" in out and "GATE 1 PASS" in out and "dry-run 임시 DB" in out
    assert not real_db.exists() and not cache.exists()

    # 본 실행
    report = tmp_path / "real" / "migration_report.json"
    assert mig.main(["--report", str(report)]) == 0
    assert real_db.exists() and json.loads(report.read_text(encoding="utf-8"))["months"]["2026-09"]["sets_equal"] is True
    conn = store.connect(str(real_db))
    store.export_all(conn, str(cache))  # 기존 cache/*.csv 가 이미 있었다고 가정(=같은 내용)
    conn.close()

    # 이제 리허설도 통과(오래된 달은 동일)하고, verify도 5개 gate 통과
    assert mig.main(["--dry-run", "--cache-dir", str(cache), "--seed", "1"]) == 0
    assert mig.main(["--verify", "--cache-dir", str(cache), "--report", str(report), "--seed", "1"]) == 0
    assert "GATE 5 PASS" in capsys.readouterr().out
    assert mig.main(["--verify", "--cache-dir", str(cache), "--report", str(report), "--skip-notion-sample"]) == 1


def _child_dbs(*pairs):
    return list(pairs)


def test_select_month_databases_requires_exactly_one_per_month():
    ok = _child_dbs(("get-ASAP 2026-08", "db-aug"), ("get-ASAP 2026-09", "db-sep"), ("Other DB", "x"),
                    ("get-ASAP 2025-01", "db-old"))  # 범위 밖 월·무관한 DB는 무시
    assert mig.select_month_databases(ok, "2026-08", "2026-09") == {"2026-08": "db-aug", "2026-09": "db-sep"}

    twin = ok + [("get-ASAP 2026-09", "db-sep-2")]  # 동명 DB: 첫 일치만 고르지 않고 중단
    with pytest.raises(mig.MigrationAbort) as e:
        mig.select_month_databases(twin, "2026-08", "2026-09")
    assert "2026-09" in str(e.value) and "db-sep" in str(e.value) and "db-sep-2" in str(e.value)

    with pytest.raises(mig.MigrationAbort) as e:  # 누락(0개)도 중단
        mig.select_month_databases(ok, "2026-08", "2026-10")
    assert "2026-10: 0개" in str(e.value)


def test_notion_source_lists_all_child_databases_across_pages():
    """NotionSource 는 부모 아래 child_database 를 페이지를 넘겨 전수 조회한다(첫 일치에서 멈추지 않는다)."""
    source = object.__new__(mig.NotionSource)
    source._parent = "parent-page"
    pages = {
        None: {"results": [{"type": "paragraph"}, {"type": "child_database", "id": "a", "child_database": {"title": "get-ASAP 2026-09"}}],
               "has_more": True, "next_cursor": "c1"},
        "c1": {"results": [{"type": "child_database", "id": "b", "child_database": {"title": "get-ASAP 2026-09"}}],
               "has_more": False, "next_cursor": None},
    }
    seen = []

    def fake_request(method, url, **kwargs):
        seen.append((method, url, kwargs.get("params")))
        return pages[kwargs["params"].get("start_cursor")]

    source._request = fake_request
    assert source.child_databases() == [("get-ASAP 2026-09", "a"), ("get-ASAP 2026-09", "b")]
    assert [s[2].get("start_cursor") for s in seen] == [None, "c1"] and "parent-page" in seen[0][1]
    with pytest.raises(mig.MigrationAbort):
        source.month_databases("2026-09", "2026-09")


def test_cli_duplicate_month_database_exits_2_before_touching_db(tmp_path, monkeypatch, capsys):
    db = tmp_path / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(db))

    class TwinSource:
        def month_databases(self, start, end):
            return mig.select_month_databases(
                [("get-ASAP 2026-09", "db1"), ("get-ASAP 2026-09", "db2")], "2026-09", "2026-09")

    monkeypatch.setattr(mig, "NotionSource", TwinSource)
    assert mig.main(["--start", "2026-09", "--end", "2026-09"]) == 2
    err = capsys.readouterr().err
    assert "중단" in err and "2026-09: 2개" in err
    assert not db.exists()


def test_cli_partition_mismatch_exits_2_and_writes_partial_report(tmp_path, monkeypatch, fake):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "asap.db"))
    fake.hide_from_date_partition.add(fake.dbs["db-2026-08"][0]["id"])
    monkeypatch.setattr(mig, "NotionSource", lambda: fake)
    report = tmp_path / "report.json"
    assert mig.main(["--report", str(report)]) == 2
    data = json.loads(report.read_text(encoding="utf-8"))
    assert "집합이 다릅니다" in data["aborted"] and data["months"]["2026-08"]["sets_equal"] is False
