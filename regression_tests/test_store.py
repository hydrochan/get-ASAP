"""store.py(정본 SQLite) 회귀 테스트 - 합성 데이터만 사용한다.

스키마·change_seq 단조성("값이 안 바뀌면 seq 불변"), 중복 판정(대소문자·대시 변형, tombstone 재유입 차단),
bucket_month 규칙, 변경 피드 페이지네이션(쓰기가 끼어들어도 누락 없음), 판정(verdict) 의미론,
CSV export(기존 _save_cache와 바이트 비교), dirty export, 백업·복원.
"""
import datetime as dt
import os
import random
import sqlite3
import uuid
from types import SimpleNamespace

import pytest

from pathlib import Path

import store
from models import PaperMetadata

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 9, 20)


def paper(title, journal="Synthetic Journal", date="2026-09-07", url="https://example.invalid/p"):
    return PaperMetadata(title=title, journal=journal, date=date, url=url)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    """오늘=2026-09-20, _now()는 호출마다 1초씩 증가(updated_at 불변 여부를 검증하기 위함)."""
    tick = {"n": 0}

    def now():
        tick["n"] += 1
        return (dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=tick["n"])).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    monkeypatch.setattr(store, "_now", now)
    monkeypatch.setattr(store, "_today", lambda: TODAY)


def save(conn, *papers):
    return store.save_papers(list(papers), conn)


def row(conn, title):
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


# ---------- 스키마 ----------

def test_schema_objects_and_pragmas(conn):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"papers", "status_history", "meta"} <= tables
    cols = [r[1] for r in conn.execute("PRAGMA table_info(papers)")]
    assert cols == [
        "id", "ref", "title", "title_key", "journal", "url", "pub_date", "bucket_month", "status", "gpt_reason",
        "status_source", "status_seq", "created_at", "updated_at", "change_seq", "deleted_at", "dup_of",
        "notion_last_edited",
    ]
    index_sql = {r[0]: r[1] for r in conn.execute("SELECT name, sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL")}
    assert "WHERE dup_of IS NULL" in index_sql["ux_papers_title_key"]
    assert "bucket_month" in index_sql["ix_papers_bucket"]
    meta = dict(conn.execute("SELECT key, value FROM meta").fetchall())
    assert meta["schema_version"] == "1" and meta["last_seq"] == "0"
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 10000


def test_check_constraints_reject_unknown_labels(conn):
    base = ("ref", "t", "t", "2026-09", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", 1)
    sql = ("INSERT INTO papers(ref,title,title_key,bucket_month,created_at,updated_at,change_seq,status,status_source) "
           "VALUES(?,?,?,?,?,?,?,?,?)")
    conn.execute(sql, base + ("관련", "human"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("r2", "t2", "t2", "2026-09", "x", "x", 2, "모르는라벨", "human"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(sql, ("r3", "t3", "t3", "2026-09", "x", "x", 3, "관련", "notion"))


def test_init_schema_is_idempotent_and_refuses_newer_schema(conn):
    store.save_papers([paper("Keep me")], conn)
    store.init_schema(conn)
    store.init_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
    assert store.last_seq(conn) == 1
    conn.execute("UPDATE meta SET value='99' WHERE key='schema_version'")
    with pytest.raises(RuntimeError):
        store.init_schema(conn)


def test_connect_missing_db_does_not_create_file(tmp_path):
    missing = tmp_path / "nope" / "asap.db"
    with pytest.raises(FileNotFoundError):
        store.connect(str(missing))
    with pytest.raises(FileNotFoundError):
        store.require_db(str(missing))
    assert not missing.exists() and not missing.parent.exists()


def test_save_papers_empty_list_never_touches_db(monkeypatch, tmp_path):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "absent.db"))
    assert store.save_papers([]) == {"saved": 0, "skipped": 0, "failed": 0}
    assert not (tmp_path / "absent.db").exists()


# ---------- change_seq ----------

def test_seq_is_monotonic_gapless_and_equals_last_seq(conn):
    store.save_papers([paper(f"P{i}") for i in range(5)], conn)
    seqs = [r[0] for r in conn.execute("SELECT change_seq FROM papers ORDER BY id")]
    assert seqs == [1, 2, 3, 4, 5]
    assert store.last_seq(conn) == 5
    store.save_papers([paper("P0"), paper("P5")], conn)  # P0 중복: seq 소비 없음
    assert store.last_seq(conn) == 6


def test_unchanged_update_does_not_bump_seq_or_touch_row(conn):
    save(conn, paper("Stable"))
    ref = row(conn, "Stable")["ref"]
    items = [{"ref": ref, "status": "관련", "gpt_reason": "because"}]
    assert store.apply_verdicts(conn, items, 0)["results"][0]["result"] == "updated"
    before = dict(row(conn, "Stable"))
    seq_before = store.last_seq(conn)
    history_before = conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]

    out = store.apply_verdicts(conn, items, 0)
    assert out["results"][0]["result"] == "unchanged"
    assert out["last_seq"] == seq_before == store.last_seq(conn)
    assert dict(row(conn, "Stable")) == before  # updated_at 포함 전혀 안 바뀜
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == history_before


def test_changed_update_bumps_exactly_once_and_logs_status_history(conn):
    save(conn, paper("Mover"))
    r0 = row(conn, "Mover")
    ref = r0["ref"]
    assert r0["status"] == "대기중" and r0["status_source"] == "get_asap" and r0["status_seq"] == r0["change_seq"] == 1

    store.apply_verdicts(conn, [{"ref": ref, "status": "관련", "gpt_reason": "r"}], 0)
    r1 = row(conn, "Mover")
    assert (r1["status"], r1["status_source"], r1["gpt_reason"]) == ("관련", "paper_autodown", "r")
    assert r1["change_seq"] == 2 == r1["status_seq"] == store.last_seq(conn)
    assert r1["updated_at"] > r0["updated_at"]
    hist = conn.execute("SELECT old_status,new_status,old_source,new_source,seq FROM status_history").fetchall()
    assert [tuple(h) for h in hist] == [("대기중", "관련", "get_asap", "paper_autodown", 2)]

    # 사유만 바뀌면 seq는 오르지만 status_seq·status_source는 그대로, 이력도 추가되지 않는다
    store.apply_verdicts(conn, [{"ref": ref, "gpt_reason": "r2"}], 0)
    r2 = row(conn, "Mover")
    assert r2["change_seq"] == 3 and r2["status_seq"] == 2 and r2["status_source"] == "paper_autodown"
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 1


def test_failed_insert_consumes_no_seq(conn, monkeypatch):
    # uuid4를 고정하면 두 번째 행이 ref UNIQUE 위반 -> failed, seq는 소비되지 않고 나머지 행은 계속 저장된다
    fixed = iter([uuid.UUID(int=1), uuid.UUID(int=1), uuid.UUID(int=2)])
    monkeypatch.setattr(store.uuid, "uuid4", lambda: next(fixed))
    result = store.save_papers([paper("A1"), paper("A2"), paper("A3")], conn)
    assert result == {"saved": 2, "skipped": 0, "failed": 1}
    assert store.last_seq(conn) == 2
    assert sorted(r[0] for r in conn.execute("SELECT change_seq FROM papers")) == [1, 2]


def test_message_batch_is_one_transaction(conn, monkeypatch):
    real = store._insert_paper
    calls = {"n": 0}

    def boom(c, rec, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise sqlite3.OperationalError("disk I/O error")
        return real(c, rec, **kw)

    monkeypatch.setattr(store, "_insert_paper", boom)
    with pytest.raises(sqlite3.OperationalError):
        store.save_papers([paper("B1"), paper("B2"), paper("B3")], conn)
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 0  # 메일 전체 롤백
    assert store.last_seq(conn) == 0 and not conn.in_transaction


# ---------- 중복 판정 ----------

@pytest.mark.parametrize("variant", [
    "Catalytic ammonia–decomposition   over Ru",  # en dash + 연속 공백
    "CATALYTIC AMMONIA-DECOMPOSITION OVER RU",
    "  catalytic ammonia‐decomposition over ru  ",  # hyphen U+2010
    "Catalytic ammonia−decomposition over Ru",  # minus sign
    "Ｃatalytic ammonia-decomposition over Ru",  # 전각 문자(NFKC)
])
def test_duplicate_variants_are_skipped(conn, variant):
    assert save(conn, paper("Catalytic ammonia-decomposition over Ru")) == {"saved": 1, "skipped": 0, "failed": 0}
    assert save(conn, paper(variant, journal="Other Journal", date="2026-05-01")) == {"saved": 0, "skipped": 1, "failed": 0}
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


def test_duplicate_scope_is_global_and_first_row_wins(conn):
    save(conn, paper("Same Title", journal="J1", date="2026-04-01", url="https://example.invalid/first"))
    # 3개월 창이 없다: 몇 달 뒤 재알림도 같은 행으로 취급, 기존 행은 바뀌지 않는다
    assert save(conn, paper("same title", journal="J2", date="2026-09-01", url="https://example.invalid/second"))["skipped"] == 1
    kept = row(conn, "Same Title")
    assert (kept["journal"], kept["url"], kept["bucket_month"]) == ("J1", "https://example.invalid/first", "2026-04")
    assert store.last_seq(conn) == 1


def test_duplicate_inside_one_batch_and_blank_titles(conn):
    result = store.save_papers([paper("Twin"), paper("TWIN"), paper("   "), paper("")], conn)
    assert result == {"saved": 1, "skipped": 3, "failed": 0}


def test_tombstone_blocks_reinsert(conn):
    save(conn, paper("Cleaned up paper"))
    r = row(conn, "Cleaned up paper")
    with store.write_tx(conn):
        assert store._update_paper(conn, r, {"deleted_at": "2026-09-21T00:00:00Z"})
    assert row(conn, "Cleaned up paper")["deleted_at"] is not None
    assert save(conn, paper("cleaned UP paper")) == {"saved": 0, "skipped": 1, "failed": 0}
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1


def test_partial_unique_index_allows_dup_rows_only(conn):
    save(conn, paper("Owner"))
    owner = row(conn, "Owner")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO papers(ref,title,title_key,bucket_month,created_at,updated_at,change_seq,status_source) "
            "VALUES('x','Owner','owner','2026-09','t','t',99,'human')"
        )
    conn.execute(
        "INSERT INTO papers(ref,title,title_key,bucket_month,created_at,updated_at,change_seq,status_source,dup_of) "
        "VALUES('x','Owner','owner','2026-09','t','t',99,'human',?)", (owner["id"],)
    )


def test_title_key_matches_legacy_notion_normalizer():
    legacy = pytest.importorskip("notion_client_mod")._normalize_title_for_duplicate
    samples = [
        "Plain title", "  Padded\ttitle\n", "A–B—C−D‐E‑F‒G", "ＦＵＬＬ　ｗｉｄｔｈ",
        "Straße vs STRASSE", "한글 제목 Mixed 123", "", None,
    ]
    for s in samples:
        assert store.normalize_title(s) == legacy(s)


# ---------- bucket_month 규칙 ----------

@pytest.mark.parametrize("pub_date,expected", [
    ("2026-09-07", "2026-09"),   # 이번 달
    ("2026-08-30", "2026-08"),   # 지난달 발행(늦게 도착한 메일)
    ("2026-04-01", "2026-04"),   # 하한 포함
    ("2026-03-31", "2026-09"),   # 하한 미만 -> 이번 달
    ("2025-12-31", "2026-09"),
    ("2026-10-05", "2026-09"),   # 미래 -> 이번 달
    ("2026-09", "2026-09"),      # 월만 있는 날짜도 월 규칙 적용
    ("2026-07", "2026-07"),
    ("2026-13-01", "2026-09"),   # 해석 불가
    ("garbage", "2026-09"),
    ("", "2026-09"),
    (None, "2026-09"),
])
def test_bucket_month_rule(pub_date, expected):
    assert store.bucket_month_for(pub_date, TODAY) == expected


def test_save_papers_assigns_bucket_and_normalizes_pub_date(conn):
    save(conn, paper("Aug one", date="2026-08-30"), paper("Old one", date="2025-01-01"),
         paper("No date", date=""), paper("Month only", date="2026-07"), paper("Timed", date="2026-09-07T10:00:00+09:00"))
    got = {r["title"]: (r["bucket_month"], r["pub_date"]) for r in conn.execute("SELECT * FROM papers")}
    assert got == {
        "Aug one": ("2026-08", "2026-08-30"),
        "Old one": ("2026-09", "2025-01-01"),   # 발행일은 보존, 버킷만 이번 달로
        "No date": ("2026-09", None),
        "Month only": ("2026-07", None),
        "Timed": ("2026-09", "2026-09-07"),
    }
    assert all(r["status_source"] == "get_asap" and r["status"] == "대기중" for r in conn.execute("SELECT * FROM papers"))


# ---------- 변경 피드 ----------

def test_changes_row_shape_and_dup_deleted_flags(conn):
    save(conn, paper("Origin"))
    origin = row(conn, "Origin")
    with store.write_tx(conn):
        store.upsert_migrated(conn, {
            "ref": "11111111-1111-1111-1111-111111111111", "title": "ORIGIN", "bucket_month": "2026-09",
            "created_at": "2026-09-01T00:00:00.000Z", "journal": "J", "url": "u", "pub_date": "2026-09-01",
        })
    feed = store.fetch_changes(conn, 0, 10)
    assert feed["has_more"] is False and feed["next_after"] == 2 and feed["last_seq"] == 2
    first, second = feed["rows"]
    assert set(first) == {
        "ref", "title", "journal", "pub_date", "url", "status", "gpt_reason", "status_source", "status_seq",
        "created_at", "bucket_month", "deleted", "dup_of_ref", "seq",
    }
    assert first["deleted"] is False and first["dup_of_ref"] is None and first["seq"] == 1
    assert second["dup_of_ref"] == origin["ref"] and second["status_source"] == "notion_migrated"


def test_changes_pagination_basics(conn):
    store.save_papers([paper(f"Row {i}") for i in range(7)], conn)
    seen, cursor = [], 0
    while True:
        page = store.fetch_changes(conn, cursor, 3)
        seen += [r["seq"] for r in page["rows"]]
        cursor = page["next_after"]
        if not page["has_more"]:
            break
    assert seen == list(range(1, 8)) and cursor == 7
    empty = store.fetch_changes(conn, 7, 3)
    assert empty == {"rows": [], "next_after": 7, "has_more": False, "last_seq": 7, "db_epoch": store.db_epoch(conn)}
    ahead = store.fetch_changes(conn, 50, 3)  # 서버보다 앞선 커서(복원된 옛 백업 등)를 last_seq로 알 수 있다
    assert ahead["rows"] == [] and ahead["last_seq"] == 7


def test_changes_has_more_is_exact_at_page_boundary(conn):
    store.save_papers([paper(f"Edge {i}") for i in range(6)], conn)
    page = store.fetch_changes(conn, 0, 3)
    assert page["has_more"] is True and page["next_after"] == 3
    last = store.fetch_changes(conn, 3, 3)
    assert last["has_more"] is False and last["next_after"] == 6 and len(last["rows"]) == 3


def test_concurrent_writers_keep_seq_unique_and_gapless(db_file):
    """서로 다른 연결(스레드)에서 삽입·판정을 동시에 해도 seq 는 중복·빈 번호 없이 커밋 순서로 부여된다."""
    import threading

    errors = []

    def writer(tag):
        c = store.connect(str(db_file))
        try:
            for i in range(15):
                store.save_papers([paper(f"{tag} paper {i}")], c)
                ref = c.execute("SELECT ref FROM papers WHERE title=?", (f"{tag} paper {i}",)).fetchone()[0]
                store.apply_verdicts(c, [{"ref": ref, "status": "관련", "gpt_reason": f"{tag}{i}"}], 0)
        except Exception as e:  # noqa: BLE001
            errors.append(e)
        finally:
            c.close()

    threads = [threading.Thread(target=writer, args=(t,)) for t in ("a", "b", "c")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    c = store.connect(str(db_file))
    try:
        seqs = [r[0] for r in c.execute("SELECT change_seq FROM papers")]
        assert store.last_seq(c) == 90  # 삽입 45 + 판정(상태 변경) 45 = 번호를 낭비하지 않았다(빈 번호 없음)
        assert len(set(seqs)) == 45 and max(seqs) <= 90  # 행마다 서로 다른 seq
        # 행마다 마지막 seq 는 그 행의 판정 시점 seq -> history 의 seq 와 일치
        assert c.execute("SELECT COUNT(*) FROM papers p JOIN status_history h ON h.paper_id=p.id AND h.seq=p.change_seq").fetchone()[0] == 45
    finally:
        c.close()


def test_changes_pagination_with_interleaved_writes_misses_nothing(conn):
    rng = random.Random(20261023)
    store.save_papers([paper(f"Seed {i}") for i in range(20)], conn)
    delivered = {}  # ref -> 마지막으로 전달된 행
    cursor, counter = 0, 0
    for step in range(200):
        page = store.fetch_changes(conn, cursor, 4)
        for r in page["rows"]:
            assert r["seq"] > cursor
            assert r["ref"] not in delivered or delivered[r["ref"]]["seq"] < r["seq"]  # 행별 seq는 단조 증가
            delivered[r["ref"]] = r
        cursor = page["next_after"]
        if step >= 30 and not page["has_more"]:
            break
        if step < 30:  # 페이지 사이에 신규 삽입·이미 전달된 행/아직 안 전달된 행 수정이 끼어든다
            action = rng.choice(["insert", "update_delivered", "update_any", "noop"])
            refs = [r[0] for r in conn.execute("SELECT ref FROM papers")]
            counter += 1
            if action == "insert":
                store.save_papers([paper(f"Late {counter}")], conn)
            elif action == "update_delivered" and delivered:
                store.apply_verdicts(conn, [{"ref": rng.choice(list(delivered)), "gpt_reason": f"edit {counter}"}], 0)
            elif action == "update_any":
                store.apply_verdicts(conn, [{"ref": rng.choice(refs), "status": rng.choice(["관련", "무관", "불확실"]),
                                             "gpt_reason": f"edit {counter}"}], 0)
    else:
        pytest.fail("페이지 순회가 끝나지 않았다")
    current = {r["ref"]: r for r in conn.execute("SELECT * FROM papers")}
    assert set(delivered) == set(current)  # 누락된 행 없음
    for ref, db_row in current.items():
        got = delivered[ref]
        assert got["seq"] == db_row["change_seq"]  # 최신 상태가 최신 seq로 전달됨
        assert (got["status"], got["gpt_reason"], got["status_source"]) == (
            db_row["status"], db_row["gpt_reason"], db_row["status_source"])
    assert cursor == store.last_seq(conn)


# ---------- 판정(verdict) ----------

def test_verdict_results_gone_invalid_and_null_semantics(conn):
    save(conn, paper("V1"), paper("V2"))
    r1, r2 = row(conn, "V1")["ref"], row(conn, "V2")["ref"]
    with store.write_tx(conn):
        store._update_paper(conn, row(conn, "V2"), {"deleted_at": "2026-09-21T00:00:00Z"})
    items = [
        {"ref": r1, "status": "관련", "gpt_reason": "x" * 5000},
        {"ref": "no-such-ref", "status": "관련"},
        {"ref": r2, "status": "관련"},                       # tombstone -> gone
        {"ref": r1, "status": "아무거나"},                    # 모르는 라벨 -> invalid
        {"ref": r1, "gpt_reason": 123},                      # 사유 타입 오류 -> invalid
        {"ref": None, "status": "관련"},
        "not-a-dict",
        {"ref": r1, "status": None, "gpt_reason": None},     # 할 일 없음 -> unchanged
    ]
    out = store.apply_verdicts(conn, items, 0)
    assert [x["result"] for x in out["results"]] == [
        "updated", "gone", "gone", "invalid", "invalid", "invalid", "invalid", "unchanged"]
    assert len(row(conn, "V1")["gpt_reason"]) == store.GPT_REASON_MAX == 4000
    # 사유만 보내면 status 유지, status만 보내면 사유 유지
    store.apply_verdicts(conn, [{"ref": r1, "gpt_reason": "short"}], 0)
    assert (row(conn, "V1")["status"], row(conn, "V1")["gpt_reason"]) == ("관련", "short")
    store.apply_verdicts(conn, [{"ref": r1, "status": "무관"}], 0)
    assert (row(conn, "V1")["status"], row(conn, "V1")["gpt_reason"]) == ("무관", "short")


def test_verdict_batch_is_idempotent_set_semantics(conn):
    save(conn, paper("I1"), paper("I2"))
    refs = [row(conn, "I1")["ref"], row(conn, "I2")["ref"]]
    items = [{"ref": refs[0], "status": "관련", "gpt_reason": "a"}, {"ref": refs[1], "status": "무관", "gpt_reason": "b"},
             {"ref": refs[0], "status": "관련", "gpt_reason": "a"}]  # 같은 ref가 배치 안에서 반복돼도 무해
    first = store.apply_verdicts(conn, items, 0)
    assert [x["result"] for x in first["results"]] == ["updated", "updated", "unchanged"]
    snapshot = [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")]
    second = store.apply_verdicts(conn, items, 0)
    assert [x["result"] for x in second["results"]] == ["unchanged"] * 3
    assert [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")] == snapshot
    assert second["last_seq"] == first["last_seq"]


def test_verdict_batch_is_atomic(conn, monkeypatch):
    save(conn, paper("T1"), paper("T2"))
    refs = [row(conn, "T1")["ref"], row(conn, "T2")["ref"]]
    real = store._apply_one_verdict
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 2:
            raise sqlite3.OperationalError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(store, "_apply_one_verdict", flaky)
    before = store.last_seq(conn)
    with pytest.raises(sqlite3.OperationalError):
        store.apply_verdicts(conn, [{"ref": refs[0], "status": "관련"}, {"ref": refs[1], "status": "관련"}], 0)
    assert store.last_seq(conn) == before and row(conn, "T1")["status"] == "대기중"


def make_human(conn, title, status="관련", status_seq=10):
    save(conn, paper(title))
    ref = row(conn, title)["ref"]
    conn.execute("UPDATE papers SET status=?, status_source='human', status_seq=? WHERE ref=?", (status, status_seq, ref))
    return ref


def test_verdict_conflict_protects_unseen_human_decision(conn):
    ref = make_human(conn, "Reviewed by hand", status="관련", status_seq=10)
    seq_before = store.last_seq(conn)
    # 사람이 판정한 뒤(seq 10) 클라이언트가 그걸 아직 못 본(seen_through 9) 상태에서 다른 status를 밀어넣으면 거부
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "무관", "gpt_reason": "bot says no"}], 9)["results"][0]
    assert res["result"] == "conflict" and res["current"]["status"] == "관련" and res["current"]["status_source"] == "human"
    assert store.last_seq(conn) == seq_before and row(conn, "Reviewed by hand")["gpt_reason"] == ""  # 사유도 미적용
    # 같은 status면 충돌이 아니다 (사유만 갱신)
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "관련", "gpt_reason": "agree"}], 9)["results"][0]
    assert res["result"] == "updated" and row(conn, "Reviewed by hand")["status_source"] == "human"
    # 이미 본 판정(seen_through >= status_seq)이면 덮어쓸 수 있고 출처는 paper_autodown으로 넘어간다
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "무관"}], 10)["results"][0]
    assert res["result"] == "updated"
    after = row(conn, "Reviewed by hand")
    assert (after["status"], after["status_source"], after["status_seq"]) == ("무관", "paper_autodown", after["change_seq"])


def test_verdict_only_notion_migrated_or_autodown_rows_never_conflict(conn):
    save(conn, paper("Auto row"))
    ref = row(conn, "Auto row")["ref"]
    conn.execute("UPDATE papers SET status_source='notion_migrated', status_seq=500 WHERE ref=?", (ref,))
    assert store.apply_verdicts(conn, [{"ref": ref, "status": "관련"}], 0)["results"][0]["result"] == "updated"


def test_verdict_hands_over_notion_migrated_source_for_reason_only_change(conn):
    """gpt_reason 만 바뀌어도 이관 행은 paper_autodown 출처가 된다(status_seq 는 그대로) - 안 그러면 이관 재실행이 덮어쓴다."""
    save(conn, paper("Migrated row"))
    ref = row(conn, "Migrated row")["ref"]
    conn.execute("UPDATE papers SET status_source='notion_migrated', status_seq=7 WHERE ref=?", (ref,))
    before = row(conn, "Migrated row")

    # 값이 안 바뀌는 판정은 출처도 건드리지 않는다
    assert store.apply_verdicts(conn, [{"ref": ref, "gpt_reason": ""}], 0)["results"][0]["result"] == "unchanged"
    assert row(conn, "Migrated row")["status_source"] == "notion_migrated"

    res = store.apply_verdicts(conn, [{"ref": ref, "gpt_reason": "bot reason"}], 0)["results"][0]
    after = row(conn, "Migrated row")
    assert res["result"] == "updated"
    assert (after["status"], after["status_source"], after["status_seq"]) == (before["status"], "paper_autodown", 7)
    assert after["change_seq"] > before["change_seq"]

    # 이관 재실행(같은 Notion 값)이 이 행을 덮어쓰지 않는다
    with store.write_tx(conn):
        assert store.upsert_migrated(conn, migrated(ref, "Migrated row", gpt_reason="notion reason")) == "protected"
    assert row(conn, "Migrated row")["gpt_reason"] == "bot reason"


def test_verdict_reason_only_never_changes_human_source_or_status_seq(conn):
    ref = make_human(conn, "Human row", status="관련", status_seq=10)
    res = store.apply_verdicts(conn, [{"ref": ref, "gpt_reason": "note"}], 0)["results"][0]
    after = row(conn, "Human row")
    assert res["result"] == "updated" and after["gpt_reason"] == "note"
    assert (after["status_source"], after["status_seq"]) == ("human", 10)


# ---------- upsert_migrated ----------

def migrated(ref, title, **kw):
    rec = {"ref": ref, "title": title, "bucket_month": "2026-08", "created_at": "2026-08-01T00:00:00.000Z",
           "journal": "J", "url": "https://example.invalid/m", "pub_date": "2026-08-01", "status": "대기중",
           "gpt_reason": "", "notion_last_edited": "2026-08-02T00:00:00.000Z"}
    rec.update(kw)
    return rec


def test_upsert_migrated_insert_update_unchanged_protected_and_dup(conn):
    a, b = "aaaaaaaa-0000-0000-0000-000000000001", "aaaaaaaa-0000-0000-0000-000000000002"
    with store.write_tx(conn):
        assert store.upsert_migrated(conn, migrated(a, "Dup Title")) == "inserted"
        assert store.upsert_migrated(conn, migrated(b, "dup title")) == "inserted"  # 중복도 보존, dup_of만 단다
    ra, rb = (conn.execute("SELECT * FROM papers WHERE ref=?", (x,)).fetchone() for x in (a, b))
    assert ra["dup_of"] is None and rb["dup_of"] == ra["id"] and ra["status_source"] == "notion_migrated"
    assert ra["created_at"] == "2026-08-01T00:00:00.000Z" and ra["updated_at"] == "2026-08-02T00:00:00.000Z"
    seq = store.last_seq(conn)
    with store.write_tx(conn):
        assert store.upsert_migrated(conn, migrated(a, "Dup Title")) == "unchanged"
        assert store.upsert_migrated(conn, migrated(a, "Dup Title", notion_last_edited="2027-01-01T00:00:00.000Z")) == "unchanged"
    assert store.last_seq(conn) == seq
    with store.write_tx(conn):
        assert store.upsert_migrated(conn, migrated(a, "Dup Title", status="관련", gpt_reason="r")) == "updated"
    assert store.last_seq(conn) == seq + 1
    # 이후 다른 출처가 건드린 행은 재이관이 덮어쓰지 않는다
    store.apply_verdicts(conn, [{"ref": a, "status": "무관"}], 0)
    with store.write_tx(conn):
        assert store.upsert_migrated(conn, migrated(a, "Dup Title", status="불확실")) == "protected"
    assert conn.execute("SELECT status FROM papers WHERE ref=?", (a,)).fetchone()[0] == "무관"


# ---------- CSV export ----------

@pytest.fixture
def excluded(monkeypatch):
    import excluded_journals

    monkeypatch.setattr(excluded_journals, "load_excluded_journals", lambda path=None: frozenset({"synthetic excluded"}))


def notion_page(title, journal, date, url, status, reason):
    """_parse_pages가 읽는 Notion API 형태의 합성 페이지."""
    props = {
        "Title": {"title": [{"plain_text": title}]},
        "Journal": {"select": {"name": journal} if journal else None},
        "Date": {"date": {"start": date} if date else None},
        "URL": {"url": url or None},
        "Status": {"select": {"name": status} if status else None},
        "GPT Reason": {"rich_text": [{"plain_text": reason}] if reason else []},
    }
    return {"properties": props}


GOLDEN_ROWS = [  # (title, journal, date, url, status, reason) - 발행일 오름차순, 날짜 없는 행은 끝(export 정렬과 동일)
    ('Plain title', 'Nat Catal', '2026-09-01', 'https://example.invalid/1', '대기중', ''),
    ('Comma, "quotes" and\nnewline', 'J. Am. Chem. Soc.', '2026-09-02', 'https://example.invalid/2?a=1&b=2', '관련', 'reason, with "quotes"\nand newline'),
    ('한글 제목 – en dash — 이모지 ✓', 'Journal of 한글', '2026-09-03', '', '불확실', '근거: 암모니아 분해'),
    ('No journal here', '', '2026-09-03', 'https://example.invalid/4', '무관', ''),
    ('Synthetic excluded article', 'Synthetic Excluded', '2026-09-04', 'https://example.invalid/5', '대기중', ''),
    ('Done paper', 'Nature', '2026-09-05', 'https://example.invalid/6', '다운완료', 'x' * 1900),
    ('Undated paper', 'Science', '', 'https://example.invalid/7', '대기중', ''),
]


def load_golden_rows(conn, rows, bucket="2026-09"):
    with store.write_tx(conn):
        for i, (title, journal, date, url, status, reason) in enumerate(rows):
            store.upsert_migrated(conn, {
                "ref": f"00000000-0000-0000-0000-{i:012d}", "title": title, "journal": journal, "url": url,
                "pub_date": date or None, "status": status, "gpt_reason": reason, "bucket_month": bucket,
                "created_at": "2026-09-01T00:00:00.000Z",
            })


def golden_bytes(rows, tmp_path):
    """기존 경로(_parse_pages -> _save_cache)가 같은 행으로 만드는 파일 바이트."""
    from analytics.notion_fetcher import _parse_pages, _save_cache

    gold_dir = tmp_path / "golden"
    _save_cache(_parse_pages([notion_page(*r) for r in rows]), "2026-09", str(gold_dir))
    return (gold_dir / "papers_2026-09.csv").read_bytes()


def test_export_csv_is_byte_identical_to_legacy_save_cache(conn, tmp_path, excluded):
    load_golden_rows(conn, GOLDEN_ROWS)
    out_dir = tmp_path / "out"
    assert store.export_csv("2026-09", conn, str(out_dir)) == len(GOLDEN_ROWS) - 1  # 제외 저널 1건 드롭
    got = (out_dir / "papers_2026-09.csv").read_bytes()
    assert got == golden_bytes(GOLDEN_ROWS, tmp_path)
    assert got.startswith(b"\xef\xbb\xbf" + b'"title","journal","date","url","status","gpt_reason"')
    assert b"Synthetic excluded" not in got


def test_export_csv_empty_bucket_matches_legacy(conn, tmp_path, excluded):
    only_excluded = [GOLDEN_ROWS[4]]
    load_golden_rows(conn, only_excluded)
    store.export_csv("2026-09", conn, str(tmp_path / "out"))
    assert (tmp_path / "out" / "papers_2026-09.csv").read_bytes() == golden_bytes(only_excluded, tmp_path)


def test_export_excludes_tombstones_includes_dup_rows(conn, tmp_path):
    with store.write_tx(conn):
        store.upsert_migrated(conn, migrated("aaaaaaaa-0000-0000-0000-000000000011", "Twin", bucket_month="2026-08"))
        store.upsert_migrated(conn, migrated("aaaaaaaa-0000-0000-0000-000000000012", "twin", bucket_month="2026-08"))
        store.upsert_migrated(conn, migrated("aaaaaaaa-0000-0000-0000-000000000013", "Gone", bucket_month="2026-08"))
        store._update_paper(conn, conn.execute("SELECT * FROM papers WHERE title='Gone'").fetchone(),
                            {"deleted_at": "2026-09-01T00:00:00Z"})
    assert store.export_csv("2026-08", conn, str(tmp_path)) == 2
    text = (tmp_path / "papers_2026-08.csv").read_text(encoding="utf-8-sig")
    assert text.count('"Twin"') == 1 and text.count('"twin"') == 1 and "Gone" not in text


def test_export_csv_is_atomic_and_leaves_no_temp_files(conn, tmp_path, monkeypatch):
    save(conn, paper("Atomic"))
    target = tmp_path / "papers_2026-09.csv"
    target.write_bytes(b"old content")
    real_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(store.os, "replace", failing_replace)
    with pytest.raises(OSError):
        store.export_csv("2026-09", conn, str(tmp_path))
    assert target.read_bytes() == b"old content" and list(tmp_path.glob("*.tmp")) == []
    monkeypatch.setattr(store.os, "replace", real_replace)
    store.export_csv("2026-09", conn, str(tmp_path))
    assert b"Atomic" in target.read_bytes() and list(tmp_path.glob("*.tmp")) == []


def test_export_csv_rejects_bad_month(conn, tmp_path):
    for bad in ("2026-9", "../2026-09", "2026-13", "", None):
        with pytest.raises(ValueError):
            store.export_csv(bad, conn, str(tmp_path))


def test_export_dirty_only_rewrites_changed_buckets(conn, tmp_path):
    cache = str(tmp_path / "cache")
    save(conn, paper("Sep one", date="2026-09-01"), paper("Aug one", date="2026-08-20"))
    first = store.export_dirty(conn, cache)
    assert first == {"2026-08": 1, "2026-09": 1}
    meta = dict(conn.execute("SELECT key, value FROM meta WHERE key LIKE 'csv_seq:%'").fetchall())
    assert meta == {"csv_seq:2026-08": "2", "csv_seq:2026-09": "1"}
    stat = {m: os.stat(store.csv_path(m, cache)).st_mtime_ns for m in first}

    assert store.export_dirty(conn, cache) == {}  # 바뀐 게 없으면 아무것도 하지 않는다
    assert {m: os.stat(store.csv_path(m, cache)).st_mtime_ns for m in first} == stat

    ref = row(conn, "Sep one")["ref"]
    store.apply_verdicts(conn, [{"ref": ref, "status": "관련"}], 0)
    assert store.export_dirty(conn, cache) == {"2026-09": 1}  # 바뀐 달만
    assert os.stat(store.csv_path("2026-08", cache)).st_mtime_ns == stat["2026-08"]
    assert '"관련"' in open(store.csv_path("2026-09", cache), encoding="utf-8-sig").read()

    store.apply_verdicts(conn, [{"ref": ref, "status": "관련"}], 0)  # 값 불변 -> seq 불변 -> export 없음
    assert store.export_dirty(conn, cache) == {}
    os.remove(store.csv_path("2026-08", cache))  # 파일이 사라지면 seq와 무관하게 복구
    assert store.export_dirty(conn, cache) == {"2026-08": 1}


def test_export_all_forces_every_bucket(conn, tmp_path):
    cache = str(tmp_path / "cache")
    save(conn, paper("A", date="2026-09-01"), paper("B", date="2026-07-01"))
    store.export_dirty(conn, cache)
    assert store.export_all(conn, cache) == {"2026-07": 1, "2026-09": 1}


def test_concurrent_exports_of_one_month_are_serialized_by_file_lock(db_file, conn, tmp_path, monkeypatch):
    """같은 월 export 두 건(대시보드 스레드·cron)이 동시에 CSV 를 쓰지 못한다 - cache/.export.lock 로 직렬화."""
    import threading
    import time

    save(conn, paper("Locked export"))
    cache = str(tmp_path / "cache")
    real_write = store._write_csv_atomic
    state = {"active": 0, "max": 0}
    guard = threading.Lock()

    def slow_write(path, rows):
        with guard:
            state["active"] += 1
            state["max"] = max(state["max"], state["active"])
        time.sleep(0.3)
        real_write(path, rows)
        with guard:
            state["active"] -= 1

    monkeypatch.setattr(store, "_write_csv_atomic", slow_write)
    barrier = threading.Barrier(2)
    errors = []

    def worker():
        c = store.connect(str(db_file))
        try:
            barrier.wait()
            store.export_csv("2026-09", c, cache)
        except BaseException as e:  # noqa: BLE001
            errors.append(e)
        finally:
            c.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert errors == [] and state["max"] == 1
    assert (tmp_path / "cache" / ".export.lock").exists()


def test_csv_seq_never_moves_backwards(conn, tmp_path):
    """늦게 끝난 오래된 스냅샷이 csv_seq 를 되돌리면 최신 변경이 export_dirty 에서 빠진다 -> MAX(기존, 새 값)."""
    save(conn, paper("Seq row"))
    cache = str(tmp_path / "cache")
    conn.execute("INSERT INTO meta(key, value) VALUES('csv_seq:2026-09', '999')")  # 더 새 스냅샷이 이미 기록했다고 가정
    store.export_csv("2026-09", conn, cache)  # 이 스냅샷의 max(change_seq)는 1
    assert conn.execute("SELECT value FROM meta WHERE key='csv_seq:2026-09'").fetchone()[0] == "999"
    store.export_csv("2026-09", conn, cache, track=False)
    assert conn.execute("SELECT value FROM meta WHERE key='csv_seq:2026-09'").fetchone()[0] == "999"


def test_refresh_csv_cli_modes(conn, tmp_path, capsys):
    import refresh_csv

    save(conn, paper("CLI one", date="2026-09-01"), paper("CLI two", date="2026-08-01"))
    assert refresh_csv.main([]) == 0
    assert "2026-08: 1 papers, 2026-09: 1 papers" in capsys.readouterr().out
    assert refresh_csv.main([]) == 0 and "변경 없음" in capsys.readouterr().out  # 바뀐 게 없으면 아무것도 안 한다
    assert refresh_csv.main(["--month", "2026-09"]) == 0 and "2026-09: 1 papers" in capsys.readouterr().out
    assert refresh_csv.main(["--start", "2026-08", "--end", "2026-09"]) == 0
    assert "2026-08: 1 papers, 2026-09: 1 papers" in capsys.readouterr().out
    assert refresh_csv.main(["--all"]) == 0 and "2026-08" in capsys.readouterr().out
    assert refresh_csv.main(["--start", "2026-08"]) == 2  # --start/--end 는 짝으로만


def test_refresh_csv_cli_without_db_fails_cleanly(monkeypatch, tmp_path, capsys):
    import refresh_csv

    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "none" / "asap.db"))
    assert refresh_csv.main([]) == 1 and "정본 DB가 없습니다" in capsys.readouterr().err
    assert not (tmp_path / "none").exists()


def test_backup_cli_backup_and_restore(db_file, conn, tmp_path, capsys):
    import backup_db

    save(conn, paper("CLI backup"))
    assert backup_db.main(["--keep", "2"]) == 0
    made = sorted((db_file.parent / "backups").glob("asap-*.db.gz"))
    assert len(made) == 1 and "백업 완료" in capsys.readouterr().out
    target = tmp_path / "back" / "asap.db"
    assert backup_db.main(["--restore", str(made[0]), "--to", str(target)]) == 0
    assert target.exists()
    assert backup_db.main(["--restore", str(made[0]), "--to", str(target)]) == 1  # 이미 있으면 거부
    assert backup_db.main(["--restore", str(made[0])]) == 2


def test_runtime_path_never_imports_notion():
    """운영 경로(main/store/sync_api/server/refresh_csv/backup_db)를 새 프로세스에서 import 해도 Notion 모듈이 로드되지 않는다."""
    import subprocess
    import sys

    code = (
        "import sys; sys.path[:0] = [%r, %r]\n"
        "import main, store, refresh_csv, backup_db, sync_api, server\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('notion_client', 'notion_client_mod', 'notion_auth') "
        "or m == 'analytics.notion_fetcher']\n"
        "print('LOADED', bad)\n"
        "sys.exit(1 if bad else 0)\n"
    ) % (str(ROOT), str(ROOT / "dashboard"))
    proc = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ---------- 백업 / 복원 ----------

def test_backup_and_restore_roundtrip(conn, tmp_path):
    import backup_db

    save(conn, paper("Backed up 1"), paper("Backed up 2", date="2026-08-01"))
    store.apply_verdicts(conn, [{"ref": row(conn, "Backed up 1")["ref"], "status": "관련", "gpt_reason": "kept"}], 0)

    raw = tmp_path / "copy.db"
    store.backup(str(raw), conn)
    check = sqlite3.connect(str(raw))
    assert check.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 2
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "delete"  # 단일 파일 사본
    check.close()

    dest_dir = tmp_path / "backups"
    stamps = [dt.datetime(2026, 9, d, 3, 0, 0, tzinfo=dt.timezone.utc) for d in range(1, 6)]
    paths = [backup_db.make_backup(str(conn.execute("PRAGMA database_list").fetchone()[2]), str(dest_dir), keep=3, now=s)
             for s in stamps]
    left = sorted(p.name for p in dest_dir.iterdir())
    assert left == [os.path.basename(p) for p in paths[-3:]]  # 최근 3개만 남음, 임시 파일 없음

    restored = tmp_path / "restored" / "asap.db"
    backup_db.restore_backup(paths[-1], str(restored))
    db2 = store.connect(str(restored))
    try:
        assert store.last_seq(db2) == store.last_seq(conn)
        assert [tuple(r) for r in db2.execute("SELECT * FROM papers ORDER BY id")] == \
            [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")]
        assert db2.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 1
        # 복원본으로 이어서 쓰기 가능(스키마·인덱스 보존)
        assert store.save_papers([paper("After restore")], db2) == {"saved": 1, "skipped": 0, "failed": 0}
        assert store.save_papers([paper("backed UP 1")], db2)["skipped"] == 1
    finally:
        db2.close()
    with pytest.raises(FileExistsError):
        backup_db.restore_backup(paths[-1], str(restored))  # 정본 덮어쓰기 거부


def test_restore_rejects_corrupt_or_foreign_files(tmp_path):
    import gzip
    import backup_db

    bad = tmp_path / "bad.db.gz"
    with gzip.open(bad, "wb") as f:
        f.write(b"this is not an sqlite database")
    with pytest.raises(Exception):
        backup_db.restore_backup(str(bad), str(tmp_path / "out.db"))
    foreign = tmp_path / "foreign.db"
    c = sqlite3.connect(str(foreign))
    c.execute("CREATE TABLE other(x)")
    c.commit()
    c.close()
    foreign_gz = tmp_path / "foreign.db.gz"
    with open(foreign, "rb") as src, gzip.open(foreign_gz, "wb") as dst:
        dst.write(src.read())
    with pytest.raises(Exception):
        backup_db.restore_backup(str(foreign_gz), str(tmp_path / "out2.db"))
    assert not (tmp_path / "out.db").exists() and not (tmp_path / "out2.db").exists()
    assert list(tmp_path.glob("*.tmp")) == []


# ---------- db_epoch (복원 감지) ----------

def test_db_epoch_is_created_once_and_reported_by_changes(conn):
    epoch = store.db_epoch(conn)
    assert len(epoch) == 36 and epoch.count("-") == 4  # uuid4
    store.init_schema(conn)  # 멱등: 다시 불러도 그대로
    assert store.db_epoch(conn) == epoch
    assert store.fetch_changes(conn, 0, 10)["db_epoch"] == epoch


def test_db_epoch_is_added_to_a_db_that_predates_it(conn):
    conn.execute("DELETE FROM meta WHERE key='db_epoch'")
    epoch = store.fetch_changes(conn, 0, 10)["db_epoch"]
    assert epoch and store.db_epoch(conn) == epoch


def test_restore_issues_a_new_db_epoch(conn, tmp_path):
    import backup_db

    save(conn, paper("Epoch row"))
    original = store.db_epoch(conn)
    gz = backup_db.make_backup(str(conn.execute("PRAGMA database_list").fetchone()[2]), str(tmp_path / "bk"))

    restored = tmp_path / "restored" / "asap.db"
    backup_db.restore_backup(gz, str(restored))
    db2 = store.connect(str(restored))
    try:
        new_epoch = store.db_epoch(db2)
        assert new_epoch != original and len(new_epoch) == 36
        assert store.last_seq(db2) == store.last_seq(conn)  # 데이터는 그대로, 식별자만 새로
    finally:
        db2.close()
    assert store.db_epoch(conn) == original  # 원본은 건드리지 않는다
