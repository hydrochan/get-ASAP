"""fix_title_subscripts.py(쪼개진 위·아래첨자 제목 일회성 정정 스크립트) 회귀 테스트 - 합성 데이터만 사용한다.

rejoin 규칙(참 양성 / 일반 문구 불변 / 멱등), 대상 선정(정본·tombstone·dup 행), 충돌 처리(기존 행·백필끼리),
title_key 재계산과 change_seq 갱신, 드라이런 무변경, --apply 시 백업 선행과 재실행 안전성을 검증한다.
"""
import csv

import pytest

import fix_title_subscripts as fts
import store

CREATED = "2026-10-29T03:00:00Z"
APPLIED_AT = "2026-11-02T09:00:00Z"


# ---------- rejoin ----------

@pytest.mark.parametrize("split, joined", [
    ("Ga 2 O 3", "Ga2O3"),
    ("NH 3", "NH3"),
    ("NaBH 4", "NaBH4"),
    ("Zn 2+", "Zn2+"),
    ("Ti 3 C 2 T x", "Ti3C2Tx"),
    ("Intensifying CO 2 desorption in nonaqueous alkanolamines", "Intensifying CO2 desorption in nonaqueous alkanolamines"),
    ("Zn–NO 3 − battery chemistry", "Zn–NO3− battery chemistry"),
    ("Cu 2 ZnSnS 4 thin-film solar cells", "Cu2ZnSnS4 thin-film solar cells"),
    ("Cu δ+ -Enriched interfaces", "Cuδ+-Enriched interfaces"),
    ("Mn( ii ) complexes for catalysis", "Mn(ii) complexes for catalysis"),
    ("n -Butyl esters from biomass", "n-Butyl esters from biomass"),
    ("Tracking 13 C in ammonia synthesis", "Tracking 13C in ammonia synthesis"),
    ("[Fe(CN) 6 ] 3− probes", "[Fe(CN)6]3− probes"),
    ("Ni(OH) 2 /NiOOH heterojunction", "Ni(OH)2/NiOOH heterojunction"),
    ("Li 7 La 3 Zr 2 O 12 garnet electrolytes", "Li7La3Zr2O12 garnet electrolytes"),
    ("LiNi 0.8 Co 0.1 Mn 0.1 O 2 cathodes", "LiNi0.8Co0.1Mn0.1O2 cathodes"),
])
def test_rejoin_joins_split_chemistry(split, joined):
    assert fts.rejoin(split) == joined


@pytest.mark.parametrize("title", [
    "Phase 2", "Type 2", "Part 2", "Group 9", "COVID 19", "In 2025", "at 4 K", "5 V", "O 2p", "HIV 1",
    "(R32) + trans",
    "Phase 2 trial of tumour response in Type 2 diabetes",
    "Results from Part 2 of the Group 9 study in 2025",
    "Annealing at 4 K and 5 V in vacuum",
    "A (R32) + trans-1,3,3,3-tetrafluoropropene refrigerant blend",
])
def test_rejoin_leaves_ordinary_text_unchanged(title):
    assert fts.rejoin(title) == title


def test_charge_after_a_closing_paren_needs_a_digit():
    assert fts.rejoin("(R32) + trans") == "(R32) + trans"
    assert fts.rejoin("[Cu(NH 3 ) 4 ] 2+ complexes") == "[Cu(NH3)4]2+ complexes"


@pytest.mark.parametrize("title", [
    "Heterostructured Ti 3 C 2 T x /Mo-CDs/NiCo-MOF nanoadditives",
    "Personal PM 2.5 -Bound metal exposure profiles",
    "In situ synthesis of SnS 2 on Ti 3 C 2 T x /g-C 3 N 4 for H 2 generation",
    "Plain text without any chemistry at all",
])
def test_rejoin_is_idempotent(title):
    once = fts.rejoin(title)
    assert fts.rejoin(once) == once


# ---------- DB ----------

@pytest.fixture
def clock(monkeypatch):
    c = {"now": CREATED}
    monkeypatch.setattr(store, "_now", lambda: c["now"])
    return c


def add(conn, clock, title, *, ref=None, bucket="2026-10", journal="Synthetic Journal"):
    """이관 경로(allow_dup)로 삽입한다: 공백만 다른 제목도 둘 다 정본으로 들어가 충돌 상황을 만들 수 있다."""
    clock["now"] = CREATED
    ref = ref or f"00000000-0000-0000-0000-{abs(hash(title)) % 10**12:012d}"
    with store.write_tx(conn):
        store._insert_paper(conn, {
            "ref": ref, "title": title, "journal": journal, "url": "https://example.invalid/p", "pub_date": "2026-10-20",
            "bucket_month": bucket, "status_source": "get_asap", "created_at": CREATED, "updated_at": CREATED,
        }, allow_dup=True)
    return row(conn, title)


def row(conn, title):
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


def tombstone(conn, title):
    conn.execute("UPDATE papers SET deleted_at=? WHERE title=?", (CREATED, title))


def seed(conn, clock):
    add(conn, clock, "Electroreduction of CO 2 to ethylene")                     # 정정 대상
    add(conn, clock, "Zn 2+ battery with long cycle life", journal="Other Journal")  # 정정 대상(다른 저널)
    add(conn, clock, "Phase 2 trial of an ordinary drug")                         # 대상 아님
    add(conn, clock, "Old tombstoned NH 3 synthesis paper")                       # tombstone 도 정정
    tombstone(conn, "Old tombstoned NH 3 synthesis paper")


def test_find_targets_picks_split_titles_in_every_row_state(conn, clock):
    seed(conn, clock)
    owner = add(conn, clock, "Duplicated H 2 production on Pt")
    add(conn, clock, "Duplicated H 2 production on Pt", ref="dup-ref")  # 정확 키가 같아 dup_of 가 달린다
    assert conn.execute("SELECT dup_of FROM papers WHERE ref='dup-ref'").fetchone()[0] == owner["id"]

    found = fts.find_targets(conn)
    assert sorted(p["old_title"] for p in found) == sorted([
        "Electroreduction of CO 2 to ethylene", "Zn 2+ battery with long cycle life",
        "Old tombstoned NH 3 synthesis paper", "Duplicated H 2 production on Pt", "Duplicated H 2 production on Pt",
    ])
    assert {p["old_title"]: p["new_title"] for p in found}["Electroreduction of CO 2 to ethylene"] == (
        "Electroreduction of CO2 to ethylene"
    )
    # 정본과 새 키가 같아지는 dup 행도 유일 인덱스 밖이라 충돌이 아니다
    assert all(p["collision"] is None for p in found)
    applied, skipped = fts.apply_fix(conn)
    assert (len(applied), skipped) == (5, [])
    assert sorted(r["title_key"] for r in conn.execute("SELECT title_key FROM papers WHERE title LIKE 'Duplicated%'")) == [
        "duplicated h2 production on pt"
    ] * 2


def test_collisions_with_existing_rows_and_between_targets_are_flagged(conn, clock):
    add(conn, clock, "Capture of CO2 in zeolites at low pressure")                    # 이미 고쳐진 정본
    add(conn, clock, "Capture of CO 2 in zeolites at low pressure")                   # existing 충돌
    add(conn, clock, "Tombstoned NH3 cracking over Ru catalysts")
    tombstone(conn, "Tombstoned NH3 cracking over Ru catalysts")
    add(conn, clock, "Tombstoned NH 3 cracking over Ru catalysts")                    # tombstone 이 점유한 키와도 충돌
    add(conn, clock, "Hydrogen storage in Mg 2 NiH 4 alloys")                         # 백필끼리 충돌: 두 변형이 같은 새 키
    add(conn, clock, "Hydrogen storage in Mg2 NiH 4 alloys")
    tombstone(conn, "Hydrogen storage in Mg 2 NiH 4 alloys")                          # tombstone 보다 살아 있는 행을 남긴다
    add(conn, clock, "Plain unrelated title about catalysis")

    by_title = {p["old_title"]: p["collision"] for p in fts.find_targets(conn)}
    assert by_title["Capture of CO 2 in zeolites at low pressure"] == "existing"
    assert by_title["Tombstoned NH 3 cracking over Ru catalysts"] == "existing"
    kept = [t for t in by_title if t.startswith("Hydrogen storage") and by_title[t] is None]
    skipped = [t for t in by_title if t.startswith("Hydrogen storage") and by_title[t] == "intra"]
    assert kept == ["Hydrogen storage in Mg2 NiH 4 alloys"] and skipped == ["Hydrogen storage in Mg 2 NiH 4 alloys"]
    assert "Plain unrelated title about catalysis" not in by_title


def test_apply_recomputes_key_bumps_seq_once_per_row_and_feeds_sync(conn, clock):
    seed(conn, clock)
    untouched = dict(row(conn, "Phase 2 trial of an ordinary drug"))
    before = store.last_seq(conn)

    clock["now"] = APPLIED_AT
    applied, skipped = fts.apply_fix(conn)

    assert sorted(p["new_title"] for p in applied) == sorted([
        "Electroreduction of CO2 to ethylene", "Zn2+ battery with long cycle life", "Old tombstoned NH3 synthesis paper",
    ])
    assert skipped == []
    r = row(conn, "Electroreduction of CO2 to ethylene")
    assert r["title_key"] == "electroreduction of co2 to ethylene"
    assert (r["updated_at"], r["created_at"]) == (APPLIED_AT, CREATED)
    assert store.last_seq(conn) == before + 3
    assert sorted(x["change_seq"] for x in conn.execute("SELECT change_seq FROM papers WHERE updated_at=?", (APPLIED_AT,))) == [
        before + 1, before + 2, before + 3,
    ]
    assert dict(row(conn, "Phase 2 trial of an ordinary drug")) == untouched
    assert row(conn, "Old tombstoned NH3 synthesis paper")["deleted_at"] == CREATED  # tombstone 은 tombstone 인 채 제목만 바뀐다
    feed = store.fetch_changes(conn, before)
    assert sorted(x["title"] for x in feed["rows"]) == sorted(p["new_title"] for p in applied)
    # 새 키가 점유되었으므로 고쳐진 파서의 제목은 재유입되지 않는다
    from models import PaperMetadata
    assert store.save_papers([PaperMetadata(title="Electroreduction of CO2 to ethylene", journal="J", date="2026-10-20")], conn)["skipped"] == 1


def test_apply_skips_collisions_and_reports_them(conn, clock):
    add(conn, clock, "Capture of CO2 in zeolites at low pressure")
    add(conn, clock, "Capture of CO 2 in zeolites at low pressure")
    add(conn, clock, "Electroreduction of CO 2 to ethylene")
    before = store.last_seq(conn)

    applied, skipped = fts.apply_fix(conn)

    assert [p["new_title"] for p in applied] == ["Electroreduction of CO2 to ethylene"]
    assert [(p["old_title"], p["collision"]) for p in skipped] == [("Capture of CO 2 in zeolites at low pressure", "existing")]
    assert row(conn, "Capture of CO 2 in zeolites at low pressure")["title_key"] == "capture of co 2 in zeolites at low pressure"
    assert store.last_seq(conn) == before + 1


def test_apply_is_idempotent(conn, clock):
    seed(conn, clock)
    add(conn, clock, "Capture of CO2 in zeolites at low pressure")
    add(conn, clock, "Capture of CO 2 in zeolites at low pressure")
    fts.apply_fix(conn)
    seq = store.last_seq(conn)
    snapshot = [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")]

    applied, skipped = fts.apply_fix(conn)

    assert applied == []
    assert [p["collision"] for p in skipped] == ["existing"]  # 충돌로 건너뛴 행만 계속 보고된다
    assert store.last_seq(conn) == seq
    assert [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")] == snapshot


# ---------- CLI ----------

def test_dry_run_changes_nothing_and_writes_the_full_report(db_file, conn, clock, tmp_path, capsys):
    seed(conn, clock)
    add(conn, clock, "Capture of CO2 in zeolites at low pressure")
    add(conn, clock, "Capture of CO 2 in zeolites at low pressure")
    seq = store.last_seq(conn)
    report = tmp_path / "report.csv"

    assert fts.main(["--report", str(report)]) == 0

    out = capsys.readouterr().out
    assert "드라이런" in out and "Synthetic Journal" in out and "Other Journal" in out
    assert "기존 행과 충돌 1" in out
    assert store.last_seq(conn) == seq
    assert row(conn, "Electroreduction of CO 2 to ethylene") is not None
    with open(report, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["id", "journal", "old_title", "new_title", "collision"]
    by_old = {r[2]: r for r in rows[1:]}
    assert len(rows) - 1 == 4
    assert by_old["Electroreduction of CO 2 to ethylene"][3:] == ["Electroreduction of CO2 to ethylene", ""]
    assert by_old["Capture of CO 2 in zeolites at low pressure"][4] == "existing"


def test_apply_backs_up_first_then_updates_and_regenerates_csv(db_file, conn, clock, tmp_path, monkeypatch):
    seed(conn, clock)
    order = []
    monkeypatch.setattr(fts.backup_db, "make_backup", lambda: order.append("backup") or "backup.gz")
    real_apply = fts.apply_fix
    monkeypatch.setattr(fts, "apply_fix", lambda c: order.append("apply") or real_apply(c))

    assert fts.main(["--apply"]) == 0

    assert order == ["backup", "apply"]
    assert row(conn, "Zn2+ battery with long cycle life") is not None
    text = (tmp_path / "cache" / "papers_2026-10.csv").read_text(encoding="utf-8")
    assert "Electroreduction of CO2 to ethylene" in text and "CO 2" not in text


def test_apply_skip_backup_flag(db_file, conn, clock, monkeypatch):
    seed(conn, clock)
    monkeypatch.setattr(fts.backup_db, "make_backup", lambda: pytest.fail("백업을 건너뛰어야 한다"))
    assert fts.main(["--apply", "--skip-backup"]) == 0


def test_main_without_db_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "missing" / "asap.db"))
    assert fts.main([]) == 1
    assert "정본 DB가 없습니다" in capsys.readouterr().err
