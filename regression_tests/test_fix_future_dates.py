"""fix_future_dates.py(미래 발행일 일회성 정정 스크립트) 회귀 테스트 - 합성 데이터만 사용한다.

대상 선정(정본 행 + pub_date > 수집일), change_seq·updated_at 갱신과 동기화 피드 노출, 옛 월 CSV dirty 처리,
드라이런 무변경, --apply 시 백업 선행, 재실행 안전성을 검증한다.
"""
import datetime as dt

import pytest

import fix_future_dates as ffd
import store
from models import PaperMetadata

CREATED = "2026-10-29T03:00:00Z"
APPLIED_AT = "2026-11-02T09:00:00Z"


@pytest.fixture
def clock(monkeypatch):
    """store._now() 고정. add()는 이 값을 created_at 으로 쓰고, 정정 시점은 테스트가 clock["now"]로 바꾼다."""
    c = {"now": CREATED}
    monkeypatch.setattr(store, "_now", lambda: c["now"])
    return c


def add(conn, clock, title, pub_date, created=CREATED, bucket=None):
    """수집 시각=created 로 한 건 저장. bucket 을 주면 bucket_month 를 강제로 바꾼다(이관 행처럼 수집월과 다른 bucket)."""
    clock["now"] = created
    store.save_papers(
        [PaperMetadata(title=title, journal="Synthetic Journal", date=pub_date)],
        conn,
        today=dt.date.fromisoformat(created[:10]),
    )
    if bucket:
        conn.execute("UPDATE papers SET bucket_month=? WHERE title=?", (bucket, title))
    return row(conn, title)


def row(conn, title):
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


def test_find_targets_selects_only_canonical_rows_dated_after_collection(conn, clock):
    add(conn, clock, "future", "2026-12-31")
    add(conn, clock, "same day", "2026-10-29")
    add(conn, clock, "past", "2026-10-01")
    add(conn, clock, "no date", "")
    add(conn, clock, "tombstone", "2026-12-31")
    conn.execute("UPDATE papers SET deleted_at=? WHERE title='tombstone'", (CREATED,))
    add(conn, clock, "dup", "2026-12-31")
    conn.execute("UPDATE papers SET dup_of=(SELECT id FROM papers WHERE title='past') WHERE title='dup'")
    add(conn, clock, "bad created_at", "2026-12-31")
    conn.execute("UPDATE papers SET created_at='0000-00-00T00:00:00Z' WHERE title='bad created_at'")

    targets = ffd.find_targets(conn)
    assert [t["title"] for t in targets] == ["future"]
    t = targets[0]
    assert (t["old_pub"], t["new_pub"], t["old_bucket"], t["new_bucket"]) == (
        "2026-12-31", "2026-10-29", "2026-10", "2026-10"
    )


def test_apply_bumps_change_seq_and_updated_at_and_reaches_sync_feed(conn, clock):
    add(conn, clock, "future", "2026-12-31")
    past = dict(add(conn, clock, "past", "2026-10-01"))
    before = store.last_seq(conn)

    clock["now"] = APPLIED_AT
    applied = ffd.apply_fix(conn)

    assert [p["title"] for p in applied] == ["future"]
    r = row(conn, "future")
    assert (r["pub_date"], r["bucket_month"]) == ("2026-10-29", "2026-10")
    assert r["change_seq"] == before + 1 == store.last_seq(conn)
    assert (r["updated_at"], r["created_at"]) == (APPLIED_AT, CREATED)
    assert dict(row(conn, "past")) == past  # 대상이 아닌 행은 seq·updated_at 포함 그대로

    feed = store.fetch_changes(conn, before)
    assert [(x["title"], x["pub_date"], x["seq"]) for x in feed["rows"]] == [("future", "2026-10-29", before + 1)]

    # 재실행해도 대상이 없고 seq 도 소비하지 않는다
    assert ffd.find_targets(conn) == [] and ffd.apply_fix(conn) == []
    assert store.last_seq(conn) == before + 1


def test_apply_is_atomic(conn, clock, monkeypatch):
    add(conn, clock, "first", "2026-12-30")
    add(conn, clock, "second", "2026-12-31")
    before = store.last_seq(conn)
    real = store._update_paper
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("Synthetic failure")
        return real(*args, **kwargs)

    monkeypatch.setattr(store, "_update_paper", flaky)
    with pytest.raises(RuntimeError, match="Synthetic failure"):
        ffd.apply_fix(conn)
    assert store.last_seq(conn) == before
    assert {r["pub_date"] for r in conn.execute("SELECT pub_date FROM papers")} == {"2026-12-30", "2026-12-31"}


def test_bucket_move_marks_old_and_new_month_dirty(conn, clock):
    add(conn, clock, "stay", "2026-09-07", created="2026-09-10T00:00:00Z")
    add(conn, clock, "moved", "2026-12-31", bucket="2026-09")  # 수집월(10월)과 다른 bucket 에 있던 행
    assert store.export_dirty(conn) == {"2026-09": 2}

    clock["now"] = APPLIED_AT
    ffd.apply_fix(conn)
    assert row(conn, "moved")["bucket_month"] == "2026-10"

    # 옛 월(09)은 max(change_seq)가 오르지 않았지만 csv_seq 메모가 지워져 dirty 로 잡힌다
    assert store.export_dirty(conn) == {"2026-09": 1, "2026-10": 1}
    assert "moved" not in open(store.csv_path("2026-09"), encoding="utf-8-sig").read()
    assert "2026-10-29" in open(store.csv_path("2026-10"), encoding="utf-8-sig").read()


def test_bucket_is_kept_when_collected_before_first_bucket_month(conn, clock):
    add(conn, clock, "early", "2026-03-05", created="2026-02-10T00:00:00Z", bucket="2026-04")
    (t,) = ffd.find_targets(conn)
    assert (t["new_pub"], t["old_bucket"], t["new_bucket"]) == ("2026-02-10", "2026-04", "2026-04")


def test_cli_dry_run_changes_nothing_and_lists_rows(conn, clock, capsys):
    add(conn, clock, "future", "2026-12-31")
    before = store.last_seq(conn)
    assert ffd.main([]) == 0
    out = capsys.readouterr().out
    assert "future" in out and "2026-12-31 -> 2026-10-29" in out and "2026-10 -> 2026-10" in out
    assert row(conn, "future")["pub_date"] == "2026-12-31" and store.last_seq(conn) == before


@pytest.mark.parametrize("skip_backup", [False, True])
def test_cli_apply_backs_up_first_and_rewrites_emptied_month_csv(conn, clock, monkeypatch, skip_backup):
    add(conn, clock, "moved", "2026-12-31", bucket="2026-09")
    store.export_dirty(conn)
    assert "moved" in open(store.csv_path("2026-09"), encoding="utf-8-sig").read()

    seen = []

    def fake_backup():
        peek = store.connect()  # 백업 시점에는 아직 정정 전이어야 한다
        try:
            seen.append(peek.execute("SELECT pub_date FROM papers WHERE title='moved'").fetchone()[0])
        finally:
            peek.close()
        return "backup.db.gz"

    monkeypatch.setattr(ffd.backup_db, "make_backup", fake_backup)
    clock["now"] = APPLIED_AT
    assert ffd.main(["--apply"] + (["--skip-backup"] if skip_backup else [])) == 0

    assert seen == ([] if skip_backup else ["2026-12-31"])
    assert row(conn, "moved")["pub_date"] == "2026-10-29"
    # 09 bucket 은 비었다 - export_dirty 가 못 잡는 경우라 스크립트가 직접 CSV 를 다시 써야 한다
    assert "moved" not in open(store.csv_path("2026-09"), encoding="utf-8-sig").read()
    assert "moved" in open(store.csv_path("2026-10"), encoding="utf-8-sig").read()
    assert store.export_dirty(conn) == {}  # 이미 최신


def test_cli_apply_without_targets_skips_backup(conn, clock, monkeypatch):
    add(conn, clock, "past", "2026-10-01")
    monkeypatch.setattr(ffd.backup_db, "make_backup", lambda: pytest.fail("백업을 호출하면 안 된다"))
    assert ffd.main(["--apply"]) == 0


def test_cli_missing_db_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "missing" / "asap.db"))
    assert ffd.main([]) == 1
    assert "정본 DB가 없습니다" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()
