"""fix_title_subscripts.py(쪼개진 위·아래첨자 제목 일회성 정정 스크립트) 회귀 테스트 - 합성 데이터만 사용한다.

rejoin 규칙(참 양성 / 일반 문구 불변 / 멱등), 대상 선정(정본·tombstone·dup 행), 충돌 처리(기존 행·백필끼리),
title_key 재계산과 change_seq 갱신, 드라이런 무변경, --apply 시 백업 선행과 재실행 안전성을 검증한다.
공백만 다른 정본 행 중복 정리(유지 행 우선순위·DOI 충돌·tombstone 그룹·export/동기화 피드 반영·멱등)도 검증한다.
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


def add(conn, clock, title, *, ref=None, bucket="2026-10", journal="Synthetic Journal", status=None, source="get_asap",
        url="https://example.invalid/p", kind=None):
    """이관 경로(allow_dup)로 삽입한다: 공백만 다른 제목도 둘 다 정본으로 들어가 충돌 상황을 만들 수 있다.
    kind 를 주면 ai_article_kind(AI 글 종류 판정)도 채운다."""
    clock["now"] = CREATED
    ref = ref or f"00000000-0000-0000-0000-{abs(hash(title)) % 10**12:012d}"
    with store.write_tx(conn):
        store._insert_paper(conn, {
            "ref": ref, "title": title, "journal": journal, "url": url, "pub_date": "2026-10-20", "status": status,
            "bucket_month": bucket, "status_source": source, "created_at": CREATED, "updated_at": CREATED,
        }, allow_dup=True)
    if kind:
        conn.execute("UPDATE papers SET ai_article_kind=? WHERE ref=?", (kind, ref))
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


# ---------- 중복 정리 (공백만 다른 정본 행) ----------

SPLIT = "Synthesis of Nb 2 O 5 -Filled carbon nanotubes"   # rejoin 이 고친다
JOINED = "Synthesis of Nb2O5-Filled carbon nanotubes"
AG_SPLIT = "Adsorption on Ag(1 1 1) surfaces"               # rejoin 이 못 고친다(Ag(111) 로 붙이지 않는다)
AG_JOINED = "Adsorption on Ag(111) surfaces"
FURFURAL = "Selective hydrogenation of furfural over Ru catalysts"  # 일반 단어 안에 공백을 넣어 만든 변형은 rejoin 이 건드리지 않는다


def furfural(i):
    """i 번째 위치에 공백을 넣은 변형(1~5). title_key 는 서로 다르지만 공백을 뺀 키는 같다."""
    w = "furfural"
    return FURFURAL.replace(w, f"{w[:i]} {w[i:]}")


def by_id(conn, paper_id):
    return conn.execute("SELECT * FROM papers WHERE id=?", (paper_id,)).fetchone()


def all_rows(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")]


def hidden_map(conn):
    """{숨겨진 행 id: keeper id}"""
    return {
        r["id"]: r["dup_of"]
        for r in conn.execute("SELECT id, dup_of FROM papers WHERE deleted_at IS NOT NULL AND dup_of IS NOT NULL")
    }


def test_split_and_joined_pair_hides_the_loser_and_fixes_the_keeper_title(conn, clock):
    keeper = add(conn, clock, SPLIT)    # 먼저 들어온 행 = keeper
    loser = add(conn, clock, JOINED)
    assert [p["collision"] for p in fts.find_targets(conn)] == ["existing"]  # 중복 정리 없이는 건너뛰던 충돌
    before = store.last_seq(conn)

    clock["now"] = APPLIED_AT
    dedupe, applied, skipped = fts.apply_all(conn)

    assert [(g["keeper"]["id"], [x["id"] for x in g["losers"]], g["reason"]) for g in dedupe["groups"]] == [
        (keeper["id"], [loser["id"]], "id")
    ]
    assert [p["new_title"] for p in applied] == [JOINED] and skipped == []
    k, lo = by_id(conn, keeper["id"]), by_id(conn, loser["id"])
    assert (k["title"], k["title_key"], k["deleted_at"], k["dup_of"]) == (JOINED, JOINED.casefold(), None, None)
    assert (lo["deleted_at"], lo["dup_of"], lo["updated_at"]) == (APPLIED_AT, keeper["id"], APPLIED_AT)
    assert [(r["status"], r["status_source"]) for r in (k, lo)] == [("대기중", "get_asap")] * 2  # 상태·출처는 그대로
    assert store.last_seq(conn) == before + 2  # 숨김 1 + keeper 제목 1


def test_hidden_loser_title_is_fixed_too(conn, clock):
    keeper = add(conn, clock, JOINED)
    loser = add(conn, clock, SPLIT)    # 나중에 들어온 쪼개진 행이 숨겨지고, 숨겨진 뒤 제목도 정정된다
    before = store.last_seq(conn)

    clock["now"] = APPLIED_AT
    dedupe, applied, skipped = fts.apply_all(conn)

    assert [x["id"] for x in dedupe["groups"][0]["losers"]] == [loser["id"]]
    assert [p["id"] for p in applied] == [loser["id"]] and skipped == []
    lo = by_id(conn, loser["id"])
    assert (lo["title"], lo["deleted_at"], lo["dup_of"]) == (JOINED, APPLIED_AT, keeper["id"])
    assert by_id(conn, keeper["id"])["dup_of"] is None
    assert store.last_seq(conn) == before + 2  # 숨김 1 + 숨긴 행 제목 1


@pytest.mark.parametrize("older, newer, newer_wins, reason", [
    # (status, status_source, ai_article_kind)
    (("다운완료", "paper_autodown", None), ("대기중", "human", None), True, "human"),     # human > 다운완료
    (("대기중", "human", None), ("다운완료", "paper_autodown", None), False, "human"),    # 순서와 무관
    (("무관", "paper_autodown", None), ("다운완료", "paper_autodown", None), True, "status"),   # 다운완료 > 무관
    (("대기중", "get_asap", None), ("무관", "paper_autodown", None), True, "status"),     # 무관 > 대기중
    (("대기중", "get_asap", "research"), ("관련", "paper_autodown", None), True, "status"),  # 상태가 판정보다 먼저
    (("관련", "paper_autodown", None), ("관련", "paper_autodown", "research"), True, "verdict"),  # 같은 상태면 판정 보유
    (("관련", "paper_autodown", "research"), ("관련", "paper_autodown", None), False, "verdict"),
    (("관련", "paper_autodown", None), ("관련", "paper_autodown", None), False, "id"),    # 전부 같으면 먼저 들어온 행
])
def test_keeper_priority(conn, clock, older, newer, newer_wins, reason):
    a = add(conn, clock, furfural(1), status=older[0], source=older[1], kind=older[2])
    b = add(conn, clock, furfural(2), status=newer[0], source=newer[1], kind=newer[2])
    (group,) = fts.find_duplicates(conn.execute("SELECT * FROM papers ORDER BY id").fetchall())["groups"]
    keeper, loser = (b, a) if newer_wins else (a, b)
    assert (group["keeper"]["id"], group["reason"]) == (keeper["id"], reason)
    before = {r["id"]: (r["status"], r["status_source"]) for r in (a, b)}

    fts.apply_all(conn)

    assert hidden_map(conn) == {loser["id"]: keeper["id"]}
    assert {i: (by_id(conn, i)["status"], by_id(conn, i)["status_source"]) for i in before} == before


def test_status_rank_order_across_all_statuses(conn, clock):
    order = ["대기중", "실패", "무관", "불확실", "관련", "다운완료"]  # 나쁜 순서대로 먼저 들어옴
    rows = [add(conn, clock, furfural(i) if i else FURFURAL, status=s) for i, s in enumerate(order)]

    fts.apply_all(conn)

    assert hidden_map(conn) == {r["id"]: rows[-1]["id"] for r in rows[:-1]}  # 다운완료 하나만 남는다


def test_doi_conflict_group_is_skipped_and_reported(conn, clock):
    a = add(conn, clock, furfural(1), url="https://doi.org/10.1000/aaa")
    b = add(conn, clock, furfural(2), url="https://doi.org/10.1000/bbb")
    # 같은 DOI(추적 링크에 인코딩되어 있어도)이거나 한쪽 DOI 가 비어 있으면 같은 논문으로 본다
    same1 = add(conn, clock, "Catalytic upgrading of lig nin oil", url="https://doi.org/10.1000/ccc")
    same2 = add(conn, clock, "Catalytic upgrading of lignin oil", url="https://t.invalid/r?u=https%3A%2F%2Fdoi.org%2F10.1000%2Fccc")
    has_doi = add(conn, clock, "Pyrolysis of cel lulose at low temperature", url="https://doi.org/10.1000/ddd")
    no_doi = add(conn, clock, "Pyrolysis of cellulose at low temperature", url="https://t.invalid/r?id=1")
    untouched = all_rows(conn)[:2]

    dedupe, applied, skipped = fts.apply_all(conn)

    assert [sorted(r["id"] for r in live) for live in dedupe["doi_conflicts"]] == [sorted([a["id"], b["id"]])]
    assert hidden_map(conn) == {same2["id"]: same1["id"], no_doi["id"]: has_doi["id"]}
    assert all_rows(conn)[:2] == untouched  # 충돌 그룹의 두 행은 그대로(제목도 정정 대상이 아니다)
    assert (applied, skipped) == ([], [])


def test_live_plus_tombstone_group_is_counted_and_untouched(conn, clock):
    add(conn, clock, "Kinetics of cellu lose pyrolysis")
    add(conn, clock, "Kinetics of cellulose pyrolysis")
    tombstone(conn, "Kinetics of cellulose pyrolysis")
    snapshot, seq = all_rows(conn), store.last_seq(conn)

    dedupe, applied, skipped = fts.apply_all(conn)

    assert (dedupe["groups"], dedupe["doi_conflicts"], dedupe["live_tombstone"]) == ([], [], 1)
    assert all_rows(conn) == snapshot and store.last_seq(conn) == seq


def test_tombstone_in_an_actionable_group_is_left_alone(conn, clock):
    a = add(conn, clock, furfural(1))
    b = add(conn, clock, furfural(2))
    t = add(conn, clock, furfural(3))
    tombstone(conn, furfural(3))
    t_before = tuple(by_id(conn, t["id"]))

    dedupe, _, _ = fts.apply_all(conn)

    assert (dedupe["live_tombstone"], hidden_map(conn)) == (0, {b["id"]: a["id"]})  # 살아 있는 두 행만 다룬다
    assert tuple(by_id(conn, t["id"])) == t_before  # tombstone 은 정본 tombstone 인 채 그대로


def test_pair_that_rejoin_cannot_fix_is_still_deduped(conn, clock):
    keeper = add(conn, clock, AG_JOINED)
    loser = add(conn, clock, AG_SPLIT)
    assert fts.find_targets(conn) == []  # 제목 정정 대상이 아니다

    dedupe, applied, skipped = fts.apply_all(conn)

    assert (applied, skipped) == ([], [])
    assert hidden_map(conn) == {loser["id"]: keeper["id"]}
    assert by_id(conn, loser["id"])["title"] == AG_SPLIT


def test_export_excludes_loser_and_sync_feed_marks_it_deleted(conn, clock):
    keeper = add(conn, clock, AG_JOINED)
    loser = add(conn, clock, AG_SPLIT)
    before = store.last_seq(conn)

    clock["now"] = APPLIED_AT
    fts.apply_all(conn)
    store.export_csv("2026-10", conn)

    text = open(store.csv_path("2026-10"), encoding="utf-8-sig").read()
    assert AG_JOINED in text and AG_SPLIT not in text
    feed = {x["ref"]: x for x in store.fetch_changes(conn, before)["rows"]}
    assert set(feed) == {loser["ref"]}  # keeper 는 바뀐 게 없다
    assert feed[loser["ref"]]["deleted"] is True and feed[loser["ref"]]["dup_of_ref"] == keeper["ref"]


def test_second_apply_is_a_no_op(conn, clock):
    add(conn, clock, SPLIT)
    add(conn, clock, JOINED)
    add(conn, clock, AG_JOINED)
    add(conn, clock, AG_SPLIT)
    add(conn, clock, furfural(1), url="https://doi.org/10.1000/aaa")   # DOI 충돌 그룹은 계속 건너뛴다
    add(conn, clock, furfural(2), url="https://doi.org/10.1000/bbb")
    clock["now"] = APPLIED_AT
    first, applied, _ = fts.apply_all(conn)
    assert (len(first["groups"]), len(applied)) == (2, 1)
    seq, snapshot = store.last_seq(conn), all_rows(conn)

    clock["now"] = "2026-11-03T09:00:00Z"
    dedupe, applied, skipped = fts.apply_all(conn)

    assert (dedupe["groups"], applied, skipped) == ([], [], [])
    assert len(dedupe["doi_conflicts"]) == 1
    assert store.last_seq(conn) == seq and all_rows(conn) == snapshot


# ---------- CLI ----------

def test_dry_run_changes_nothing_and_writes_the_full_report(db_file, conn, clock, tmp_path, capsys):
    seed(conn, clock)
    add(conn, clock, "Capture of CO2 in zeolites at low pressure")
    tombstone(conn, "Capture of CO2 in zeolites at low pressure")                     # tombstone 이 점유한 키
    add(conn, clock, "Capture of CO 2 in zeolites at low pressure")
    seq = store.last_seq(conn)
    report = tmp_path / "report.csv"

    assert fts.main(["--report", str(report)]) == 0

    out = capsys.readouterr().out
    assert "드라이런" in out and "Synthetic Journal" in out and "Other Journal" in out
    assert "기존 행과 충돌 1" in out
    assert "숨길 그룹 0개" in out and "tombstone 인 그룹 1개" in out
    assert store.last_seq(conn) == seq
    assert row(conn, "Electroreduction of CO 2 to ethylene") is not None
    with open(report, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    assert rows[0] == ["id", "journal", "old_title", "new_title", "collision", "action", "keeper_id"]
    by_old = {r[2]: r for r in rows[1:]}
    assert len(rows) - 1 == 4
    assert by_old["Electroreduction of CO 2 to ethylene"][3:] == ["Electroreduction of CO2 to ethylene", "", "fix_title", ""]
    assert by_old["Capture of CO 2 in zeolites at low pressure"][4] == "existing"


def test_dry_run_reports_dedupe_stats_and_writes_hide_dup_rows(db_file, conn, clock, tmp_path, capsys):
    older = add(conn, clock, SPLIT, status="대기중")
    newer = add(conn, clock, JOINED, status="다운완료", source="paper_autodown")   # 상태 순위로 keeper
    add(conn, clock, furfural(1), url="https://doi.org/10.1000/aaa")
    add(conn, clock, furfural(2), url="https://doi.org/10.1000/bbb")
    add(conn, clock, "Kinetics of cellu lose pyrolysis")
    add(conn, clock, "Kinetics of cellulose pyrolysis")
    tombstone(conn, "Kinetics of cellulose pyrolysis")
    snapshot, seq = all_rows(conn), store.last_seq(conn)
    report = tmp_path / "report.csv"

    assert fts.main(["--report", str(report)]) == 0

    out = capsys.readouterr().out
    assert "숨길 그룹 1개 -> 숨길 행 1건" in out
    assert "DOI 가 달라 건너뛴 그룹 1개" in out and "tombstone 인 그룹 1개" in out
    assert "상태 순위 1" in out
    assert "상태가 서로 다른 그룹 1개" in out
    assert f"id={newer['id']} status=다운완료 status_source=paper_autodown [유지]" in out
    assert f"id={older['id']} status=대기중 status_source=get_asap [숨김]" in out
    assert "드라이런, 중복 정리를 반영한 뒤 기준" in out
    assert all_rows(conn) == snapshot and store.last_seq(conn) == seq  # 드라이런은 아무것도 쓰지 않는다
    with open(report, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    hide = [r for r in rows[1:] if r[5] == "hide_dup"]
    assert hide == [[str(older["id"]), "Synthetic Journal", SPLIT, "", "", "hide_dup", str(newer["id"])]]
    # 제목 정정은 중복 정리를 반영한 상태 기준: 숨겨질 쪼개진 행은 충돌이 아니다
    assert [(r[0], r[3], r[4]) for r in rows[1:] if r[5] == "fix_title" and r[0] == str(older["id"])] == [(str(older["id"]), JOINED, "")]


def test_apply_regenerates_months_of_both_phases_and_second_run_skips_backup(db_file, conn, clock, monkeypatch):
    keeper = add(conn, clock, AG_JOINED, bucket="2026-10")
    loser = add(conn, clock, AG_SPLIT, bucket="2026-09")   # 제목 정정이 없어도 숨긴 행의 월 CSV 는 다시 써야 한다
    store.export_csv("2026-09", conn)
    store.export_csv("2026-10", conn)
    assert AG_SPLIT in open(store.csv_path("2026-09"), encoding="utf-8-sig").read()
    backups = []
    monkeypatch.setattr(fts.backup_db, "make_backup", lambda: backups.append(1) or "backup.gz")

    assert fts.main(["--apply"]) == 0

    assert hidden_map(conn) == {loser["id"]: keeper["id"]}
    assert AG_SPLIT not in open(store.csv_path("2026-09"), encoding="utf-8-sig").read()
    assert AG_JOINED in open(store.csv_path("2026-10"), encoding="utf-8-sig").read()
    seq, snapshot = store.last_seq(conn), all_rows(conn)

    assert fts.main(["--apply"]) == 0

    assert backups == [1] and store.last_seq(conn) == seq and all_rows(conn) == snapshot


def test_apply_backs_up_first_then_updates_and_regenerates_csv(db_file, conn, clock, tmp_path, monkeypatch):
    seed(conn, clock)
    order = []
    monkeypatch.setattr(fts.backup_db, "make_backup", lambda: order.append("backup") or "backup.gz")
    real_apply = fts.apply_all
    monkeypatch.setattr(fts, "apply_all", lambda c: order.append("apply") or real_apply(c))

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
