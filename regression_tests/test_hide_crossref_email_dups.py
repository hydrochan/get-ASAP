"""hide_crossref_email_dups.py: Crossref 행과 겹치는 메일 행을 숨기고 Crossref 행을 남기는 일회성 스크립트 (합성 DB)."""
import csv

import pytest

import hide_crossref_email_dups as hd
import store

OLD = "2026-06-01T00:00:00Z"   # 메일로 들어온 행
NEW = "2026-11-23T10:00:00Z"   # Crossref 백필로 들어온 행
SINCE = "2026-11-23T09:00:00Z"
J = "ACS Central Science"
BASE = "Engineering a selective electrocatalyst for nitrate reduction to ammonia"

CONFIG = {"journals": [
    {"journal": J, "issn": ["2374-7943"], "mode": "ingest"},
    {"journal": "Journal of the American Chemical Society", "issn": ["0002-7863"], "mode": "ingest"},
    {"journal": "JACS", "issn": ["0002-7863"], "mode": "ingest", "alias_of": "Journal of the American Chemical Society"},
    {"journal": "Shadow Journal", "issn": ["1111-1111"], "mode": "shadow"},
]}


@pytest.fixture
def clock(monkeypatch):
    c = {"now": "2026-11-23T11:00:00Z"}
    monkeypatch.setattr(store, "_now", lambda: c["now"])
    return c


def add(conn, title, *, created=NEW, journal=J, url=None, status="대기중", source="get_asap", kind=None, kind_source=None, n=[0]):
    n[0] += 1
    url = url if url is not None else f"https://doi.org/10.1021/new{n[0]}"
    with store.write_tx(conn):
        store._insert_paper(conn, {
            "ref": f"00000000-0000-0000-0000-{n[0]:012d}", "title": title, "journal": journal, "url": url,
            "pub_date": "2026-10-20", "status": status, "bucket_month": "2026-10", "status_source": source,
            "created_at": created, "updated_at": created,
        }, allow_dup=True)
    if kind:
        conn.execute("UPDATE papers SET ai_article_kind=?, article_kind_source=? WHERE title=?", (kind, kind_source, title))
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


def state(conn, row):
    r = conn.execute("SELECT * FROM papers WHERE id=?", (row["id"],)).fetchone()
    return r["deleted_at"], r["dup_of"]


def test_title_match_kinds_and_series_guard():
    assert hd.title_match(BASE, BASE.upper() + " †") == "alnum"
    assert hd.title_match(BASE, BASE + " The conversion of nitrate to ammonia is studied with new catalysts") == "prefix"
    assert hd.title_match(BASE, BASE.replace("selective", "selectiv")) == "fuzzy"
    long_title = "Polymer electrolytes for lithium batteries: structure and transport, Part I"
    assert hd.title_match(long_title, long_title + "I") is None          # Part I / Part II
    assert hd.title_match(long_title, long_title[:-1] + "2") is None     # Part I / Part 2
    assert hd.title_match(BASE, "A completely different title about perovskite solar cells") is None


def test_abstract_row_is_hidden_and_crossref_row_kept(db_file, conn, clock):
    old = add(conn, BASE + " The conversion of nitrate to ammonia is studied", created=OLD, url="https://click.example/x", status="무관")
    new = add(conn, BASE)
    before = store.last_seq(conn)

    plan = hd.find_pairs(conn, CONFIG, SINCE)
    assert [(p["action"], p["match"]) for p in plan["pairs"]] == [("hide_email", "prefix")]
    assert store.last_seq(conn) == before          # 드라이런은 아무것도 쓰지 않는다

    hd.apply_pairs(conn, CONFIG, SINCE)
    assert state(conn, new) == (None, None)
    assert state(conn, old) == ("2026-11-23T11:00:00Z", new["id"])
    r = conn.execute("SELECT status, status_source FROM papers WHERE id=?", (old["id"],)).fetchone()
    assert (r["status"], r["status_source"]) == ("무관", "get_asap")   # 상태는 그대로

    seq = store.last_seq(conn)
    assert hd.apply_pairs(conn, CONFIG, SINCE)["pairs"] == []     # 재실행은 아무것도 하지 않는다
    assert store.last_seq(conn) == seq


def test_same_doi_with_different_title_is_matched(db_file, conn, clock):
    old = add(conn, "Totally different wording of the very same paper", created=OLD, url="https://doi.org/10.1021/same")
    new = add(conn, BASE, url="https://doi.org/10.1021/SAME")
    plan = hd.find_pairs(conn, CONFIG, SINCE)
    assert [(p["match"], p["old"]["id"], p["new"]["id"]) for p in plan["pairs"]] == [("doi", old["id"], new["id"])]


def test_different_dois_are_skipped_even_when_titles_match(db_file, conn, clock):
    add(conn, BASE, created=OLD, url="https://doi.org/10.1021/aaa")
    add(conn, BASE.replace("selective", "selectiv"), url="https://doi.org/10.1021/bbb")
    plan = hd.find_pairs(conn, CONFIG, SINCE)
    assert plan["pairs"] == [] and len(plan["doi_conflicts"]) == 1


def test_part_one_and_part_two_are_not_merged(db_file, conn, clock):
    base = "Polymer electrolytes for lithium batteries: structure and transport, Part "
    add(conn, base + "I", created=OLD, url="https://click.example/1")
    add(conn, base + "II")
    assert hd.find_pairs(conn, CONFIG, SINCE)["pairs"] == []


def test_human_touched_email_row_is_kept_and_crossref_row_hidden(db_file, conn, clock):
    old = add(conn, BASE + " The conversion of nitrate to ammonia is studied", created=OLD, url="https://click.example/h",
              status="관련", source="human")
    new = add(conn, BASE)
    plan = hd.apply_pairs(conn, CONFIG, SINCE)
    assert [p["action"] for p in plan["pairs"]] == ["hide_crossref"]
    assert state(conn, old) == (None, None)
    assert state(conn, new) == ("2026-11-23T11:00:00Z", old["id"])


def test_human_article_kind_and_review_history_also_protect(db_file, conn, clock):
    a = add(conn, "Alpha catalysts for selective hydrogenation of aromatic compounds with abstract text appended", created=OLD,
            url="https://click.example/a", kind="research", kind_source="human")
    b = add(conn, "Beta membranes for selective gas separation in humid conditions with abstract text appended", created=OLD, url="https://click.example/b")
    conn.execute("INSERT INTO status_history(paper_id, old_status, new_status, old_source, new_source, seq, at, actor) "
                 "VALUES (?, '대기중', '관련', 'get_asap', 'human', 1, ?, 'admin')", (b["id"], OLD))
    add(conn, "Alpha catalysts for selective hydrogenation of aromatic compounds")
    add(conn, "Beta membranes for selective gas separation in humid conditions")
    assert {p["old"]["id"]: p["action"] for p in hd.find_pairs(conn, CONFIG, SINCE)["pairs"]} == {
        a["id"]: "hide_crossref", b["id"]: "hide_crossref"}


def test_scope_alias_other_journals_and_old_crossref_side(db_file, conn, clock):
    alias_old = add(conn, BASE + " with abstract text appended", created=OLD, journal="JACS", url="https://click.example/j")
    new_jacs = add(conn, BASE, journal="Journal of the American Chemical Society")   # 별칭은 정식명과 같은 저널
    add(conn, "Perovskite films with record stability under operating conditions", created=OLD, journal="Shadow Journal", url="https://x/1")
    add(conn, "Perovskite films with record stability under operating conditions", journal="Shadow Journal")  # ingest 저널 아님
    add(conn, "Another journal row with an identical title to the first one", created=OLD, journal="Other", url="https://x/2")
    add(conn, "Another journal row with an identical title to the first one", journal=J)   # 다른 저널과는 짝이 아니다
    add(conn, "Email row that arrived after the backfill started with the same title", created="2026-11-23T09:30:00Z", url="https://x/3")
    add(conn, "Email row that arrived after the backfill started with the same title", url="https://click.example/late")  # 새 행이 doi.org 아님
    plan = hd.find_pairs(conn, CONFIG, SINCE)
    assert [(p["old"]["id"], p["new"]["id"]) for p in plan["pairs"]] == [(alias_old["id"], new_jacs["id"])]
    assert hd.find_pairs(conn, CONFIG, SINCE, ["acs central science"])["pairs"] == []


def test_old_row_matched_by_two_new_rows_is_linked_once(db_file, conn, clock):
    old = add(conn, BASE + " Abstract text that continues for a while here", created=OLD, url="https://click.example/o")
    add(conn, BASE, url="https://doi.org/10.1021/n1")
    add(conn, BASE[:-3], url="https://doi.org/10.1021/n2")
    plan = hd.find_pairs(conn, CONFIG, SINCE)
    assert len(plan["pairs"]) == 1 and len(plan["ambiguous"]) == 1 and plan["pairs"][0]["old"]["id"] == old["id"]


# ---------- CLI ----------

def cfg_file(tmp_path):
    import json
    p = tmp_path / "cfg.json"
    p.write_text(json.dumps(CONFIG), encoding="utf-8")
    return str(p)


def test_cli_dry_run_writes_csv_and_apply_backs_up_first(db_file, conn, clock, tmp_path, monkeypatch, capsys):
    old = add(conn, BASE + " The conversion of nitrate to ammonia is studied", created=OLD, url="https://click.example/x")
    new = add(conn, BASE)
    report = tmp_path / "pairs.csv"
    cfg = cfg_file(tmp_path)

    assert hd.main(["--since-created", SINCE, "--config", cfg, "--report", str(report)]) == 0
    assert "드라이런" in capsys.readouterr().out
    assert state(conn, old) == (None, None)
    with open(report, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert [(r["action"], r["match"], r["old_id"], r["new_id"]) for r in rows] == [("hide_email", "prefix", str(old["id"]), str(new["id"]))]

    order = []
    monkeypatch.setattr(hd.backup_db, "make_backup", lambda: order.append("backup") or "backup.gz")
    real = hd.apply_pairs
    monkeypatch.setattr(hd, "apply_pairs", lambda *a: order.append("apply") or real(*a))
    assert hd.main(["--since-created", SINCE, "--config", cfg, "--apply"]) == 0
    assert order == ["backup", "apply"]
    assert state(conn, old)[1] == new["id"]


def test_cli_without_db_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "missing" / "asap.db"))
    assert hd.main(["--since-created", SINCE]) == 1
    assert "정본 DB가 없습니다" in capsys.readouterr().err


def test_since_created_must_be_a_timestamp():
    with pytest.raises(SystemExit):
        hd.parse_args(["--since-created", "yesterday"])
