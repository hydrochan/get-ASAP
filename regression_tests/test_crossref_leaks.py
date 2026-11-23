"""2026-11-23 Crossref 비논문 유출 규칙(crossref_source editorial-title)과 정리 스크립트(purge_crossref_leaks.py) 회귀 테스트.

제목·DOI 는 운영 DB 에서 실제로 새어 들어온 항목이고, 음성 사례는 같은 출판사의 실제 연구 논문 제목이다(합성 DB·네트워크 없음).
"""
import csv

import pytest

import crossref_source as cs
import purge_crossref_leaks as pg
import store

T_ACS = "10.1021/acsnano.6c18590"
T_CELL = "10.1016/j.chempr.2026.103338"


def item(doi, title, **extra):
    d = {"DOI": doi, "title": [title], "type": "journal-article"}
    d.update(extra)
    return d


@pytest.mark.parametrize("doi,title", [
    # ACS 편집자 글
    ("10.1021/acsnano.6c18590", "Celebrating 20 Years of Suzhou Institute of Nano-Tech and Nano-Bionics (SINANO), Chinese Academy of Sciences"),
    ("10.1021/acsphotonics.6c00001", "Announcing the Winner of the 2027 ACS Photonics Young Investigator Lectureship Award: Lina Quan"),
    ("10.1021/acs.jpcc.6c04550", "Tribute to Mark Ratner"),
    ("10.1021/acs.jpcc.6c00002", "A Tribute to Jacek Lipkowski"),
    ("10.1021/acsami.6c00003", "Interview with Benjamin Droguet, Co-Founder of Sparxell"),
    ("10.1021/acsnano.6c00004", "A Conversation with Prof. Paul S. Weiss, Founding Editor-in-Chief of ACS Nano and Nanoscience Pioneer"),
    ("10.1021/acs.est.6c00005", "60 Years of ES&T in Pursuit of Sustainable Systems"),
    ("10.1021/acs.est.6c00006", "Policy Analysis at ES&T: New Science, New Technologies, and New Policy Needs"),
    ("10.1021/acs.iecr.6c03113", "Special Issue: 2025 Class of Influential Researchers"),
    ("10.1021/acs.jpcc.6c00007", "Special Issue on Photophysics of Materials"),
    ("10.1021/acssuschemeng.6c00008", "Nobel Symposium 2025: The Future of Chemical Safety and Sustainable Materials Chemistry"),
    ("10.1021/jacsau.6c01047", "JACSAu Appoints the Sixth Early Career Advisory Board"),
    ("10.1021/jacsau.6c01009", "Introducing the JACS Au Perspective Collection Series"),
    ("10.1021/acsaem.6c02581", "Editor’s Highlights in Perovskite Solar Cells 2024−2025"),
    ("10.1021/acscatal.6c02972", "What’s New at ACS Catalysis: From New Team Members, a New Advisory Board Structure to New Processes for Review"),
    ("10.1021/acs.iecr.6c00009", "Advances in the Optimization of Process Operations – In Memory of Pedro Castro"),
    # ACS 정정 (update-to 없이 등록된 것)
    ("10.1021/acsomega.6c11800", "Addition and Correction to “Numerical Investigation of LPG Leakage Dispersion and Explosion Consequences”"),
    ("10.1021/acssuschemeng.6c00010", "Addition and Correction for “Synergistic Antibacterial Activity of Ultralow Loading Atomically Dispersed Cu and Ag Clusters”"),
    # Cell Press
    ("10.1016/j.chempr.2026.103338", "Chem at 10: Athina Anastasaki"),
    ("10.1016/j.chempr.2026.103132", "Reaction: From decorative dimers to structurally honest photocatalysis"),
    ("10.1016/j.chempr.2026.103130", "Catalyst: Confronting the β–O–4 obsession in lignin photocatalysis"),
    ("10.1016/j.cell.2026.00.001", "SnapShot: The hallmarks of cancer treatment"),
    ("10.1016/j.isci.2026.116900", "Editor Note to article entitled “Advanced slime mould algorithm incorporating differential evolution and Powell mechanism”"),
    # Wiley / RSC
    ("10.1002/smll.202600001", "Comment on: Modeling of a Single‐Band‐Ratiometric Sensor Based on Lattice Positive Thermal Expansion in Eu 3+ ‐Activated Phosphors"),
    ("10.1039/d6cp90114j", "Structure and dynamics of chemical systems: Honouring N. Sathyamurthy's 75th birthday"),
    ("10.1039/d6sc90197b", "A focus on caging radicals: a catch-and-release mechanism for BODIPY photocages"),
    ("10.1039/d6ta90188c", "Introduction to advanced aqueous batteries"),
])
def test_editorial_titles_are_rejected(doi, title):
    assert cs.skip_reason(item(doi, title), title) == "editorial-title"


@pytest.mark.parametrize("doi,title", [
    # ACS 연구 논문: 같은 단어가 있어도 앞머리·구절이 다르다
    ("10.1021/acsomega.6c0001", "Introducing 2-Thioxocoumarins as a New Class of Photosensitizers for Antimicrobial Photodynamic Therapy"),
    ("10.1021/acsnano.6c00002", "Introducing Chirality into Graphene Oxide Layers through a Facile Amino Acid-Based Covalent Functionalization"),
    ("10.1021/acs.jpcc.6c00003", "Snapshot Redox Cycling Voltammetry (Snapshot RCV): Fast Generation-Collection Detection across a Wide Potential Range"),
    ("10.1021/acsami.6c00004", "Interview-Based Qualitative Study of Hydrogen Safety Perceptions Among Operators"),
    ("10.1021/acs.est.6c00005", "Special Cases of Nitrate Reduction on Copper Electrodes in Neutral Media"),
    ("10.1021/acs.est.6c12890", "Urban Nitrogen Policy Should Follow Pathways, Not the Calendar"),   # 정규식으로 못 거르는 ACS 사설은 통과한다(AI 판정이 숨김)
    # 출판사 한정 규칙은 다른 출판사 DOI 에는 적용되지 않는다
    ("10.1016/j.apcatb.2026.1", "Reaction: kinetics of ammonia decomposition on Ru catalysts under pressure"),
    ("10.1016/j.nanoen.2026.1", "Celebrating the discovery of piezotronics in Nano Energy materials for wearable sensing"),
    ("10.1002/smll.202600002", "Response of Perovskite Nanocrystals to Humidity: Comment on Stability Trends in Small Devices"),
    # RSC 연구 논문 번호 (첫 자리 0/1)
    ("10.1039/d6cp04123a", "Effect of solvent on proton transfer dynamics in water clusters on metal surfaces"),
    ("10.1039/d6sc10456b", "Catalytic asymmetric synthesis of axially chiral biaryl compounds via cooperative catalysis"),
])
def test_research_lookalikes_pass(doi, title):
    assert cs.skip_reason(item(doi, title), title) is None


def test_editorial_rules_apply_to_unscoped_doi_for_global_labels_only():
    assert cs.skip_reason(item("10.1/x", "Snapshot: the state of hydrogen storage research"), "Snapshot: the state of hydrogen storage research") == "editorial-title"
    assert cs.skip_reason(item("10.1/x", "Celebrating twenty years of discoveries"), "Celebrating twenty years of discoveries") is None  # ACS 한정


# ---------- 정리 스크립트 ----------

NOW = "2026-11-23T11:00:00Z"


@pytest.fixture
def clock(monkeypatch):
    monkeypatch.setattr(store, "_now", lambda: NOW)


def add(conn, title, doi, *, journal="ACS Nano", status="대기중", source="get_asap", url=None, month="2026-10", n=[0]):
    n[0] += 1
    with store.write_tx(conn):
        store._insert_paper(conn, {
            "ref": f"00000000-0000-0000-0000-{n[0]:012d}", "title": title, "journal": journal,
            "url": url or f"https://doi.org/{doi}", "pub_date": "2026-10-20", "status": status, "bucket_month": month,
            "status_source": source, "created_at": "2026-11-23T10:00:00Z", "updated_at": "2026-11-23T10:00:00Z",
        }, allow_dup=True)
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


def deleted(conn, row):
    return conn.execute("SELECT deleted_at FROM papers WHERE id=?", (row["id"],)).fetchone()[0]


@pytest.fixture
def seeded(conn):
    rows = {
        "editorial": add(conn, "Tribute to Mark Ratner", "10.1021/acs.jpcc.6c04550", journal="The Journal of Physical Chemistry C"),
        "research": add(conn, "Catalytic ammonia decomposition over Ru nanoparticles on carbon supports", "10.1021/acscatal.6c00001"),
        "rsc_ed": add(conn, "Introduction to wound healing materials", "10.1039/d6tb90148d", journal="Journal of Materials Chemistry B"),
        "relevant": add(conn, "Celebrating 20 Years of Suzhou Institute of Nano-Tech", "10.1021/acsnano.6c18590", status="관련"),
        "human": add(conn, "Interview with Our Two New PI Viewpoint Authors", "10.1021/acs.accounts.6c00002", source="human", status="무관"),
        "mail": add(conn, "Tribute to J. Karl Johnson", "x", url="https://click.example.com/abc"),  # 메일 행(doi.org 아님)은 대상이 아니다
        "nc_noassert": add(conn, "Q&A World immunisation week 2026", "10.1038/s41467-026-72493-1", journal="Nature Communications"),
        "nc_ok": add(conn, "Structural basis of a membrane transporter in complex with a nanobody", "10.1038/s41467-026-70001-2", journal="Nature Communications"),
        "nc_unfetched": add(conn, "A note without any fetched assertion data in the cache or API", "10.1038/s41467-026-79999-9", journal="Nature Communications"),
    }
    cache = {
        "10.1038/s41467-026-72493-1": [],
        "10.1038/s41467-026-70001-2": ["received", "accepted"],
    }
    return rows, cache


def test_find_targets_dry_run_classifies_and_changes_nothing(conn, seeded):
    rows, cache = seeded
    before = conn.execute("SELECT COUNT(*), MAX(change_seq) FROM papers").fetchone()[:]
    plan = pg.find_targets(conn, cache)
    assert {t["row"]["id"] for t in plan["tombstone"]} == {rows[k]["id"] for k in ("editorial", "rsc_ed", "relevant", "nc_noassert")}
    assert [t["row"]["id"] for t in plan["human"]] == [rows["human"]["id"]]
    assert [t["row"]["id"] for t in plan["unverified"]] == [rows["nc_unfetched"]["id"]]
    assert {t["reason"] for t in plan["tombstone"]} == {"editorial-title", "nature-not-peer-reviewed"}
    assert conn.execute("SELECT COUNT(*), MAX(change_seq) FROM papers").fetchone()[:] == before


def test_apply_tombstones_once_keeps_human_and_is_idempotent(conn, seeded, clock):
    rows, cache = seeded
    plan = pg.apply_plan(conn, cache)
    assert len(plan["tombstone"]) == 4
    for k in ("editorial", "rsc_ed", "relevant", "nc_noassert"):
        assert deleted(conn, rows[k]) == NOW
    for k in ("research", "human", "mail", "nc_ok", "nc_unfetched"):
        assert deleted(conn, rows[k]) is None
    seq = conn.execute("SELECT MAX(change_seq) FROM papers").fetchone()[0]
    again = pg.apply_plan(conn, cache)
    assert again["tombstone"] == []
    assert conn.execute("SELECT MAX(change_seq) FROM papers").fetchone()[0] == seq
    assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    # 동기화 피드에 deleted 로 실린다
    r = conn.execute("SELECT change_seq FROM papers WHERE id=?", (rows["editorial"]["id"],)).fetchone()
    assert r[0] > 0


def test_collect_assertions_fetches_only_missing_and_merges_cache(conn, seeded, tmp_path):
    rows, cache = seeded
    path = tmp_path / "cache.json"
    path.write_text('{"10.1038/s41467-026-70001-2": ["received"]}', encoding="utf-8")
    calls = []

    def fake_fetch(dois):
        calls.append(sorted(dois))
        return {d.lower(): [] for d in dois}

    urls = conn.execute("SELECT url FROM papers WHERE url LIKE 'https://doi.org/%'").fetchall()
    out = pg.collect_assertions(urls, str(path), fetch=fake_fetch)
    assert calls == [["10.1038/s41467-026-72493-1", "10.1038/s41467-026-79999-9"]]
    assert out["10.1038/s41467-026-70001-2"] == ["received"]
    assert "10.1038/s41467-026-72493-1" in pg.load_cache(str(path))  # 합쳐 저장


def test_report_csv_lists_actions(conn, seeded, tmp_path):
    _, cache = seeded
    path = tmp_path / "r.csv"
    pg.write_report(pg.find_targets(conn, cache), str(path))
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert {r["action"] for r in rows} == {"tombstone", "human", "unverified"}
    assert sum(r["action"] == "tombstone" for r in rows) == 4


def test_main_dry_run_then_apply_regenerates_csv(db_file, conn, seeded, tmp_path, monkeypatch, clock):
    rows, cache = seeded
    cache_path = tmp_path / "c.json"
    cache_path.write_text('{"10.1038/s41467-026-72493-1": [], "10.1038/s41467-026-70001-2": ["received"], "10.1038/s41467-026-79999-9": ["accepted"]}', encoding="utf-8")
    monkeypatch.setattr(pg.backup_db, "make_backup", lambda: str(tmp_path / "b.db.gz"))
    exported = []
    monkeypatch.setattr(store, "export_csv", lambda month, conn=None, *a, **k: exported.append(month))
    assert pg.main(["--assertions-cache", str(cache_path)]) == 0
    assert deleted(conn, rows["editorial"]) is None  # 드라이런
    assert pg.main(["--apply", "--assertions-cache", str(cache_path)]) == 0
    assert deleted(conn, rows["editorial"]) == NOW
    assert exported == ["2026-10"]
    assert pg.main(["--apply", "--assertions-cache", str(cache_path)]) == 0  # 재실행: 대상 없음
