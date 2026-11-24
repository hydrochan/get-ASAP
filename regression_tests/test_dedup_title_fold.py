"""저장 시 중복 판정(title_fold·DOI)과 일회성 정리 스크립트(dedup_cleanup.py) 회귀 테스트 - 합성 데이터만 사용한다.

운영 DB 에서 확인된 실제 패턴(Ga2O3·TiO2 쌍, 추적 URL -> DOI URL, 끝 마침표·하이픈·HTML 표기 차이)을 고정 입력으로 재현하고,
서로 다른 논문(비슷한 제목·DOI 가 다른 같은 제목·너무 짧은 제목)은 갈라져야 한다는 점을 함께 검증한다.
"""
import sqlite3

import pytest

import crossref_source
import dedup_cleanup as dc
import store
from models import PaperMetadata

TRACKING = "http://url5675.acs.org/ls/click?upn=u001.FAKE-TRACKING-TOKEN"
CREATED = "2026-10-29T03:00:00Z"


def paper(title, journal="Synthetic Journal", url="https://example.invalid/p", date="2026-10-20"):
    return PaperMetadata(title=title, journal=journal, date=date, url=url)


def save(conn, *papers):
    return store.save_papers(list(papers), conn, today=store._dt.date(2026, 11, 20))


def titles(conn):
    return [r["title"] for r in conn.execute("SELECT title FROM papers WHERE dup_of IS NULL ORDER BY id")]


# ---------- 키 함수 ----------

@pytest.mark.parametrize("a, b", [
    ("2D small hole polarons in Ga 2 O 3", "2D Small Hole Polarons in Ga2O3"),
    ("2D–2D TiO 2 /anthracene-based COF for H 2 generation", "2D-2D TiO2/Anthracene-based COF for H2 generation."),
    ("Fabric for synergistic dualmode thermal management", "Fabric for synergistic dual-mode thermal management"),
    ("Cu<sub>2</sub>O &amp; ZnO nanowires for CO<sub>2</sub> reduction", "Cu2O & ZnO nanowires for CO2 reduction"),
    ("Ultrafast Spectroscopy with an Edge Basic Concepts", "Ultrafast Spectroscopy with an Edge ─Basic Concepts"),
])
def test_title_fold_equates_notation_variants(a, b):
    assert store.title_fold(a) == store.title_fold(b)


@pytest.mark.parametrize("a, b", [
    ("Pt catalysts for CO oxidation", "Pt catalysts for CO2 oxidation"),
    ("Ni 2 P nanosheets for HER", "Ni 3 P nanosheets for HER"),
])
def test_title_fold_keeps_different_papers_apart(a, b):
    assert store.title_fold(a) != store.title_fold(b)


@pytest.mark.parametrize("url, doi", [
    ("https://doi.org/10.1021/acs.nanolett.6c02220", "10.1021/acs.nanolett.6c02220"),
    ("https://dx.doi.org/10.1021/ACS.NanoLett.6C02220", "10.1021/acs.nanolett.6c02220"),
    ("https://doi.org/10.1002/anie.202400001/full", "10.1002/anie.202400001"),
    ("https://onlinelibrary.wiley.com/doi/10.1002/anie.202400001/abs/?utm=1", "10.1002/anie.202400001"),
    ("https://doi.org/10.1000/xyz.", "10.1000/xyz"),
    ("https://doi.org/10.1000/fullerene-paper", "10.1000/fullerene-paper"),  # DOI 자체가 full 로 시작해도 꼬리가 아니다
    (TRACKING, ""),
    ("", ""),
    (None, ""),
])
def test_doi_key(url, doi):
    assert store.doi_key(url) == doi


# ---------- save_papers: 같은 논문은 막는다 ----------

def test_ga2o3_pair_tracking_url_then_doi_url_is_skipped_and_keeps_first_status(conn):
    save(conn, paper("2D Small Hole Polarons in Ga 2 O 3", url=TRACKING))
    conn.execute("UPDATE papers SET status='다운완료', status_source='paper_autodown'")

    res = save(conn, paper("2D small hole polarons in Ga2O3", url="https://doi.org/10.1021/acs.nanolett.6c02220"))

    assert res == {"saved": 0, "skipped": 1, "failed": 0}
    rows = conn.execute("SELECT status, status_source FROM papers").fetchall()
    assert [tuple(r) for r in rows] == [("다운완료", "paper_autodown")]  # 먼저 들어온 행의 상태가 그대로


def test_tio2_pair_differing_in_subscript_spaces_and_trailing_period_is_skipped(conn):
    save(conn, paper("2D–2D TiO 2 /anthracene-based COF for visible-light photocatalytic H 2 generation", url=TRACKING))
    res = save(conn, paper("2D-2D TiO2/Anthracene-based COF for visible-light photocatalytic H2 generation."))
    assert res["saved"] == 0 and res["skipped"] == 1 and len(titles(conn)) == 1


def test_same_doi_with_different_title_text_is_skipped(conn):
    doi_url = "https://doi.org/10.1021/acs.chemrev.6c00098"
    save(conn, paper("Computational Mass Spectrometry Imaging in the Era of AI", url=doi_url))
    res = save(conn, paper("Computational Mass Spectrometry Imaging in the Era of AIFocus", url=doi_url + "/full"))
    assert res["saved"] == 0 and res["skipped"] == 1


def test_html_markup_and_dash_variants_are_skipped(conn):
    save(conn, paper("Cu<sub>2</sub>O &amp; ZnO nanowires for CO<sub>2</sub> reduction"))
    assert save(conn, paper("Cu2O & ZnO nanowires for CO2 reduction"))["skipped"] == 1


def test_duplicate_inside_one_batch_is_skipped(conn):
    res = save(conn, paper("Ni 2 P nanosheets for the hydrogen evolution reaction"),
               paper("Ni2P nanosheets for the hydrogen evolution reaction."))
    assert (res["saved"], res["skipped"]) == (1, 1)


def test_tombstone_still_blocks_reentry_by_fold(conn):
    save(conn, paper("Ni 2 P nanosheets for the hydrogen evolution reaction"))
    conn.execute("UPDATE papers SET deleted_at=?", (CREATED,))
    assert save(conn, paper("Ni2P nanosheets for the hydrogen evolution reaction."))["skipped"] == 1


def test_same_title_in_another_journal_is_the_same_paper(conn):
    """저널 표기가 달라도(운영 데이터의 'ACS Physical Chem Au' / 'ACS Physical Chemistry Au') 제목이 같으면 한 논문 -
    기존 title_key 유일 인덱스도 저널을 보지 않는다."""
    save(conn, paper("A Minimalist Model for Navigating Conformational Landscape", journal="ACS Physical Chemistry Au", url=TRACKING))
    res = save(conn, paper("A Minimalist Model for Navigating Conformational Landscape.", journal="ACS Physical Chem Au"))
    assert res["skipped"] == 1


# ---------- save_papers: 다른 논문은 갈라둔다 ----------

def test_similar_but_different_titles_are_both_saved(conn):
    res = save(conn, paper("Pt catalysts for CO oxidation at low temperature"),
               paper("Pt catalysts for CO2 oxidation at low temperature"),
               paper("Pt catalysts for CO oxidation at high temperature"))
    assert res["saved"] == 3


def test_same_title_with_different_dois_is_not_merged(conn):
    save(conn, paper("Comment on Hydrogen storage in porous frameworks", url="https://doi.org/10.1000/aaa"))
    res = save(conn, paper("Comment on: Hydrogen storage in porous frameworks", url="https://doi.org/10.1000/bbb"))
    assert res["saved"] == 1 and len(titles(conn)) == 2


def test_very_short_folded_titles_need_an_exact_key_match(conn):
    res = save(conn, paper("Preface"), paper("Preface."), paper("Preface"))
    assert (res["saved"], res["skipped"]) == (2, 1)  # 정확 키가 같은 건 기존대로 스킵, 짧은 제목의 표기 차이는 합치지 않는다


def test_index_cache_is_invalidated_by_writes_outside_store(conn):
    save(conn, paper("Nitrogen reduction on Fe 3 O 4 surfaces"))
    conn.execute(
        "INSERT INTO papers (ref, title, title_key, journal, url, bucket_month, status_source, created_at, updated_at, change_seq)"
        " VALUES ('x', 'Direct insert paper about CO 2 capture', 'direct insert paper about co 2 capture', '', '', '2026-10', 'get_asap', ?, ?, 999)",
        (CREATED, CREATED),
    )
    conn.execute("UPDATE meta SET value=999 WHERE key='last_seq'")
    assert save(conn, paper("Direct insert paper about CO2 capture."))["skipped"] == 1
    assert save(conn, paper("Nitrogen reduction on Fe3O4 surfaces."))["skipped"] == 1


def test_rolled_back_batch_does_not_leave_phantom_rows_in_the_cached_index(conn, monkeypatch):
    save(conn, paper("Seed paper on ammonia decomposition catalysts"))
    real = store._insert_paper
    calls = {"n": 0}

    def boom(c, rec, **kw):
        calls["n"] += 1
        out = real(c, rec, **kw)
        if calls["n"] == 2:
            raise sqlite3.OperationalError("disk I/O error")
        return out

    monkeypatch.setattr(store, "_insert_paper", boom)
    with pytest.raises(sqlite3.OperationalError):
        save(conn, paper("Ghost paper that gets rolled back one"), paper("Ghost paper that gets rolled back two"))
    monkeypatch.setattr(store, "_insert_paper", real)
    assert save(conn, paper("Ghost paper that gets rolled back one"))["saved"] == 1


# ---------- Crossref 사전 조회표 ----------

def test_crossref_title_index_ignores_punctuation_but_not_short_titles():
    index = crossref_source.TitleIndex()
    index.add_title("2D–2D TiO 2 /anthracene-based COF for H 2 generation", "2026-10-01")
    assert index.lookup("2D-2D TiO2/Anthracene-based COF for H2 generation.") == "2026-10-01"
    index.add_title("Preface", "2026-10-02")
    assert index.lookup("Preface.") is None
    assert index.lookup("Preface") == "2026-10-02"


# ---------- dedup_cleanup ----------

def add(conn, title, *, journal="Synthetic Journal", url="", status="무관", source="get_asap", bucket="2026-10", ref=None):
    """정리 스크립트 입력용 정본 행. 중복 판정을 거치지 않도록 allow_dup=True + 서로 다른 title_key 로 넣는다."""
    ref = ref or f"ref-{title}-{url}"
    with store.write_tx(conn):
        store._insert_paper(conn, {
            "ref": ref, "title": title, "journal": journal, "url": url, "pub_date": "2026-10-20", "status": status,
            "bucket_month": bucket, "status_source": source, "created_at": CREATED, "updated_at": CREATED,
        }, allow_dup=True)
    return conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()


def snapshot(conn):
    return [tuple(r) for r in conn.execute("SELECT * FROM papers ORDER BY id")], store.last_seq(conn)


def seed_cleanup(conn):
    ga_old = add(conn, "2D Small Hole Polarons in Ga 2 O 3", url=TRACKING, status="무관")
    ga_new = add(conn, "2D small hole polarons in Ga2O3", url="https://doi.org/10.1021/acs.nanolett.6c02220", status="다운완료")
    ti_old = add(conn, "2D–2D TiO 2 /anthracene COF for photocatalytic H 2 generation", url=TRACKING + "2")
    ti_new = add(conn, "2D-2D TiO2/Anthracene COF for photocatalytic H2 generation.", url=TRACKING + "3")
    doi_a = add(conn, "Computational Imaging in the Era of AI", url="https://doi.org/10.1021/x.1")
    doi_b = add(conn, "Computational Imaging in the Era of AIFocus", url="https://doi.org/10.1021/x.1/full")
    j1 = add(conn, "Minimalist Model for Conformational Landscape", journal="ACS Physical Chemistry Au", url=TRACKING + "4")
    j2 = add(conn, "Minimalist Model for Conformational Landscape.", journal="ACS Physical Chem Au", url=TRACKING + "5")
    d1 = add(conn, "Hydride storage benchmarking across families", status="다운완료", source="paper_autodown")
    d2 = add(conn, "Hydride storage benchmarking across families.", status="다운완료", source="notion_migrated")
    # 건드리면 안 되는 행들
    add(conn, "Pt catalysts for CO oxidation at low temperature")
    add(conn, "Pt catalysts for CO2 oxidation at low temperature")
    add(conn, "Comment on Hydrogen storage in porous frameworks", url="https://doi.org/10.1000/aaa")
    add(conn, "Comment on: Hydrogen storage in porous frameworks", url="https://doi.org/10.1000/bbb")
    add(conn, "Preface"), add(conn, "Preface.")
    return dict(ga=(ga_old, ga_new), ti=(ti_old, ti_new), doi=(doi_a, doi_b), j=(j1, j2), dd=(d1, d2))


def test_plan_groups_keeper_rules_and_skips_non_duplicates(conn):
    s = seed_cleanup(conn)
    plan = dc.plan(conn)

    by_keeper = {g["keeper"]["id"]: g for g in plan["groups"]}
    assert len(plan["groups"]) == 5
    ga = by_keeper[s["ga"][1]["id"]]  # 다운완료가 이긴다(id 가 더 커도)
    assert [r["id"] for r in ga["losers"]] == [s["ga"][0]["id"]] and ga["reason"] == "status"
    assert by_keeper[s["ti"][0]["id"]]["kind"] == "punct_html" and by_keeper[s["ti"][0]["id"]]["reason"] == "id"
    assert by_keeper[s["doi"][0]["id"]]["kind"] == "doi_only"
    assert by_keeper[s["j"][0]["id"]]["losers"][0]["journal"] == "ACS Physical Chem Au"
    assert by_keeper[s["dd"][0]["id"]]["reason"] == "id"
    # DOI 가 다른 Comment 쌍은 건너뛰고 보고만, CO/CO2 쌍·짧은 Preface 쌍은 묶이지도 않는다
    assert [r["title"][:10] for r in plan["doi_conflicts"][0]] == ["Comment on", "Comment on"] and len(plan["doi_conflicts"]) == 1
    touched = {r["title"] for g in plan["groups"] for r in g["rows"]}
    assert not any(t.startswith(("Pt catalysts", "Comment on", "Preface")) for t in touched)


def test_dry_run_changes_nothing_and_reports_inconsistent_and_double_downloads(db_file, conn, capsys, tmp_path):
    seed_cleanup(conn)
    before = snapshot(conn)
    report = tmp_path / "hide.csv"

    assert dc.main(["--report", str(report)]) == 0

    out = capsys.readouterr().out
    assert "숨길 그룹 5개 -> 숨길 행 5건" in out
    assert "공백만 다름 1 / 구두점·HTML·하이픈 표기 차이 3 / DOI 만 같음 1" in out
    assert "서로 다른 DOI 라 건너뛴 그룹 1개" in out
    assert "다운완료/무관 1" in out and "다운완료/다운완료 1" in out
    assert "상태가 엇갈려 다운완료 행을 유지하는 그룹 1개" in out
    assert "다운완료가 둘 이상인 그룹 1개" in out and "source=paper_autodown" in out
    assert "저널 표기가 서로 다른 그룹 1개" in out
    assert snapshot(conn) == before
    lines = report.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines) == 1 + 5


def test_apply_hides_losers_keeps_status_backs_up_and_is_idempotent(db_file, conn, monkeypatch):
    s = seed_cleanup(conn)
    store.export_csv("2026-10", conn)
    backups = []
    monkeypatch.setattr(dc.backup_db, "make_backup", lambda db=None: backups.append(db) or "backup.gz")

    assert dc.main(["--apply"]) == 0

    assert len(backups) == 1
    for old, new in (s["ga"], s["ti"], s["doi"], s["j"], s["dd"]):
        pair = {r["id"]: conn.execute("SELECT * FROM papers WHERE id=?", (r["id"],)).fetchone() for r in (old, new)}
        keeper = min(pair.values(), key=lambda r: r["dup_of"] is not None)
        loser = next(r for r in pair.values() if r["dup_of"] is not None)
        assert loser["dup_of"] == keeper["id"] and loser["deleted_at"] is not None
    ga_old = conn.execute("SELECT * FROM papers WHERE id=?", (s["ga"][0]["id"],)).fetchone()
    assert (ga_old["status"], ga_old["status_source"]) == ("무관", "get_asap")  # 상태는 건드리지 않는다
    csv_text = open(store.csv_path("2026-10"), encoding="utf-8-sig").read()
    assert "Ga 2 O 3" not in csv_text and "Ga2O3" in csv_text and "Preface." in csv_text
    after = snapshot(conn)

    assert dc.main(["--apply"]) == 0

    assert len(backups) == 1 and snapshot(conn) == after  # 숨길 게 없으면 백업도 쓰기도 없다


def test_doi_conflict_groups_are_reported_not_hidden(conn, capsys):
    add(conn, "Hydrogen storage in porous frameworks", url="https://doi.org/10.1000/aaa")
    add(conn, "Hydrogen storage in porous frameworks.", url="https://doi.org/10.1000/bbb")
    # 같은 제목이지만 DOI 가 다른 두 행은 묶이지만(같은 title_fold) 건너뛴다
    plan = dc.plan(conn)
    assert plan["groups"] == [] and len(plan["doi_conflicts"]) == 1
