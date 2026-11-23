"""Crossref 2단계 준비: Nature 비논문 규칙, Wiley 표지, 영숫자 제목 키(dedup·shadow), Angewandte ISSN."""
import datetime as dt

import pytest

import crossref_fetch as cf
import crossref_source as cs
import store
from models import PaperMetadata

TODAY = dt.date(2026, 10, 29)
JOURNAL = "Nature Communications"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(cs, "_today_utc", lambda: TODAY)
    monkeypatch.setattr(store, "_today", lambda: TODAY)


def item(doi, title, created="2026-10-27T02:16:13Z", **extra):
    d = {
        "DOI": doi,
        "title": [title],
        "type": "journal-article",
        "created": {"date-parts": [[int(x) for x in created[:10].split("-")]], "date-time": created},
    }
    d.update(extra)
    return d


def add_row(conn, title, journal=JOURNAL, created_at="2026-10-20T00:00:00Z"):
    store.save_papers([PaperMetadata(title=title, journal=journal, date="2026-10-20")], conn, today=TODAY)
    conn.execute("UPDATE papers SET created_at=? WHERE title=?", (created_at, title))


# Crossref 가 실제로 주는 모양(received/accepted 는 ArticleHistory 그룹)
REVIEWED = [
    {"value": "10 November 2025", "order": 1, "name": "received", "label": "Received",
     "group": {"name": "ArticleHistory", "label": "Article History"}},
    {"value": "13 May 2026", "order": 2, "name": "accepted", "label": "Accepted",
     "group": {"name": "ArticleHistory", "label": "Article History"}},
    {"value": "15 July 2026", "order": 3, "name": "first_online", "label": "First Online",
     "group": {"name": "ArticleHistory", "label": "Article History"}},
]
NO_HISTORY = [{"value": "No competing interests.", "order": 1, "name": "Ethics", "label": "Ethics"}]


# ---------- Nature ----------

def test_assertion_is_selected_in_every_query():
    # 빠지면 모든 Nature 논문이 nature-not-peer-reviewed 로 버려진다
    assert "assertion" in cs.SELECT_FIELDS.split(",")
    assert "assertion" in cs.SELECT_FIELDS_MULTI.split(",")


@pytest.mark.parametrize("it, title, reason", [
    (item("10.1038/s41467-026-1", "Alpha beta gamma delta study", assertion=REVIEWED), "Alpha beta gamma delta study", None),
    (item("10.1038/S41467-026-1", "Alpha beta gamma delta study", assertion=REVIEWED), "Alpha beta gamma delta study", None),
    (item("10.1038/s41467-026-2", "Alpha beta gamma delta study", assertion=NO_HISTORY), "Alpha beta gamma delta study", "nature-not-peer-reviewed"),
    (item("10.1038/s41467-026-3", "Alpha beta gamma delta study", assertion=[]), "Alpha beta gamma delta study", "nature-not-peer-reviewed"),
    (item("10.1038/s41467-026-4", "Alpha beta gamma delta study"), "Alpha beta gamma delta study", "nature-not-peer-reviewed"),
    (item("10.1038/D41586-026-5", "Alpha beta gamma delta study", assertion=REVIEWED), "Alpha beta gamma delta study", "nature-magazine"),
    (item("10.1038/d41586-026-5", "Alpha beta gamma delta study"), "Alpha beta gamma delta study", "nature-magazine"),
    # 기존 판정이 먼저
    (item("10.1038/s41467-026-6", "Alpha beta gamma delta", assertion=REVIEWED, **{"update-to": [{"DOI": "10.1/y"}]}), "Alpha beta gamma delta", "update-to"),
    (item("10.1038/s41467-026-7", "Author Correction: Alpha beta gamma", assertion=REVIEWED), "Author Correction: Alpha beta gamma", "correction-title"),
    # 심사 확인된 Nature 는 짧은 제목도 통과(실제 Article 3건), 라벨·Reply 는 그대로 걸린다
    (item("10.1038/s41586-026-8", "Multilevel molecular weaving", assertion=REVIEWED), "Multilevel molecular weaving", None),
    (item("10.1038/s41586-026-9", "Altermagnetic photonic crystals", assertion=REVIEWED), "Altermagnetic photonic crystals", None),
    (item("10.1038/s41467-026-10", "Reply to: Alpha beta gamma delta", assertion=REVIEWED), "Reply to: Alpha beta gamma delta", "filters"),
    (item("10.1038/s41467-026-11", "Front Cover: Alpha beta", assertion=REVIEWED), "Front Cover: Alpha beta", "filters"),
    # 다른 출판사는 규칙 밖: assertion 이 없어도 통과, 짧은 제목은 기존대로 short-title
    (item("10.1021/acs.1", "Alpha beta gamma delta study"), "Alpha beta gamma delta study", None),
    (item("10.1002/anie.1", "Alpha beta gamma delta study", assertion=NO_HISTORY), "Alpha beta gamma delta study", None),
    (item("10.1021/acs.2", "Multilevel molecular weaving"), "Multilevel molecular weaving", "short-title"),
])
def test_nature_skip_rules(it, title, reason):
    assert cs.skip_reason(it, title) == reason


def test_nature_labels_flow_into_partition_non_research(conn):
    items = [
        item("10.1038/s41467-026-1", "Research paper one on catalysts", assertion=REVIEWED),
        item("10.1038/d41586-026-2", "News feature about science policy today"),
        item("10.1038/s41467-026-3", "Spotlight without peer review history here", assertion=NO_HISTORY),
        item("10.1038/s41586-026-4", "Multilevel molecular weaving", assertion=REVIEWED),
    ]
    part = cs.partition_items(items, JOURNAL, cs.load_index(conn, [JOURNAL], global_exact=True, since_created=None), TODAY)
    assert [p.title for p in part.new] == ["Research paper one on catalysts", "Multilevel molecular weaving"]
    assert sorted(r for r, _ in part.non_research) == ["nature-magazine", "nature-not-peer-reviewed"]


# ---------- Wiley 표지 ----------

@pytest.mark.parametrize("title", [
    "Flexible Dielectric Acoustic Resonator Patch for Tissue Regeneration (Adv. Mater. 43/2026)",
    "Osmotic Energy Harvesting From Composition Tuned Laminar Membranes (Small 43/2026)",
    "Synergistic Catalytic Mechanism (Adv. Funct. Mater. 62/2026)",
    "Chelation Drives Surface Substitution in Hybrid‐MXenes (Angew. Chem. Int. Ed. 37/2026)",
    "Carbon Dots for Color‐Changing Hydrogels (Adv. Energy Mater. 5/2026) ",
    "Diffraction‐Free Omnidirectional Antireflection Binary Metasurface via Femtosecond Laser Hybrid Etching (Adv. Mater. 28//2026)",
])
def test_wiley_cover_titles_are_skipped(title):
    assert cs.skip_reason(item("10.1002/adma.1", title), title) == "cover-title"


@pytest.mark.parametrize("title", [
    "Modeling of the 2/2026 election cycle in networks",
    "Phase diagram of a spin 1/2 chain (S = 1/2) under field",
    "Catalysis at 3/2 filling in moire systems (Phys. Rev. B)",
    "Alpha beta gamma delta study (2026)",
])
def test_non_cover_titles_pass(title):
    assert cs.skip_reason(item("10.1002/adma.1", title), title) is None


# ---------- Science 본지 ----------

@pytest.mark.parametrize("it, title, reason", [
    (item("10.1126/science.zzz1", "Kennedy rejects NIH director's pick for mental health institute", assertion=[{"name": "published"}]),
     "Kennedy rejects NIH director's pick for mental health institute", "science-not-peer-reviewed"),
    (item("10.1126/science.zzz2", "Use common standards to stop risky research"), "Use common standards to stop risky research",
     "science-not-peer-reviewed"),
    (item("10.1126/science.aem1", "Programmable lattice assembly of nanocrystal superstructures", assertion=REVIEWED),
     "Programmable lattice assembly of nanocrystal superstructures", None),
    (item("10.1126/science.aem2", "Quantum-Cutting Metals", assertion=REVIEWED), "Quantum-Cutting Metals", None),  # 짧은 제목도 동료심사 확인되면 통과
    # Science Advances·Signaling 은 규칙 대상이 아니다(신규 항목이 모두 연구 논문이었다)
    (item("10.1126/sciadv.abc1", "Emergent patterns of island biodiversity in the Anthropocene"),
     "Emergent patterns of island biodiversity in the Anthropocene", None),
])
def test_science_skip_rules(it, title, reason):
    assert cs.skip_reason(it, title) == reason


# ---------- 영숫자 키 ----------

# (메일 제목, Crossref 제목) - 2026-11 메일 대 Crossref 비교에서 제목 키가 달라 못 맞춘 실제 쌍 중 영숫자 키로 맞는 것
STRICT_PAIRS = [
    ("Asymmetric Au(I)/Au(III) Catalysis with Chiral bis(P'̂'N) Ligands Deriving from Tröger’s Bases",
     "Asymmetric Au(I)/Au(III) Catalysis with Chiral bis(P∧N) Ligands Deriving from Tröger’s Bases"),
    ("Band-like Transport with Hall Mobility >70 cm2 V–1 s–1 in Stoichiometric Perovskite Crystals",
     "Band-like Transport with Hall Mobility &gt;70 cm2 V–1 s–1 in Stoichiometric Perovskite Crystals"),
    ("Cement-Based Composites for Microwave Absorption: From Material Design to Architectural Integration&#xe5f8;A Review",
     "Cement-Based Composites for Microwave Absorption: From Material Design to Architectural IntegrationA Review"),
    ("A Single-Component Iridium Photocatalyst for Rapid NADH Re-generation and Photo-Biocatalysis",
     "A single-component iridium photocatalyst for rapid NADH regeneration and photo-biocatalysis"),
    ("Frequency-dependent dielectric behavior of aerosol-jet printed nanodiamond thin films",
     "Frequency-dependent dielectric behavior of aerosol jet-printed nanodiamond thin films"),
    ("A sulfinate–azide hybrid reagent for sequential radical fluoroalkylation and click reaction †",
     "A sulfinate–azide hybrid reagent for sequential radical fluoroalkylation and click reaction"),
    ("2,2′-Dithiotolane pincer ligands with an electronically non-innocent central π-C ≡ C ligating unit and their coordination chemistry",
     "2,2′-Dithiotolane pincer ligands with an electronically non-innocent central π-CC ligating unit and their coordination chemistry"),
    ("Recent modernization in g-C3N4/MoS2 derived heterojunctions for photocatalytic applications †",
     "Recent modernization in g-C3N4 /MoS2 derived heterojunctions for photocatalytic applications"),
    ("Tuning the Local Pd Environment through MIL-53(Al) Linker Engineering for Enantioselective C=C Hydrogenation",
     "Tuning the local Pd environment through MIL-53(Al) linker engineering for enantioselective CC hydrogenation"),
    ("Electron-ion confinement enabled by 3D Cu framework and lithiophilic LiSi alloy for high-performance lithium metal batteries",
     "Electron-ion confinement enabled by 3D Cu framework and lithiophilic Li-Si alloy for high-performance lithium metal batteries"),
    ("Safeguarding global terrestrial vertebrate species from future sea-level rise",
     "Safeguarding global terrestrial vertebrate species from future sea level rise"),
]

# shadow 에서만 쓰는 접두·유사도 일치 쌍
LOOSE_PAIRS = [
    ("When Lipoproteins Meet Vesicles: Rethinking the LDL Protein CoronaLDL-associated proteomes may reflect both a corona and lipoprotein-EV interactions.",
     "When Lipoproteins Meet Vesicles: Rethinking the LDL Protein Corona"),
    ("Inflammation-targeting biomimetic hybrid membrane S1nanocarriers modulate disulfide bond and lipid homeostasis for osteoarthritis therapy",
     "Inflammation-targeting biomimetic hybrid membrane nanocarriers modulate disulfide bond and lipid homeostasis for osteoarthritis therapy"),
    ("Two dimensional Co3O4 nanosheets with prior (112) facets for efficient catalytic ozone decomposition",
     "Two dimensional Co3O4 nanosheets with prior (112) facets for efficiently catalytic ozone decomposition"),
    ("A piezo-nanozyme theragnostic platform: Ultrasound-parameter-dependent regulation for concurrent melanoma suppression and surgical repair",
     "A piezo-nanozyme therapeutic platform: Ultrasound-parameter-dependent regulation for concurrent melanoma suppression and surgical repair"),
]


@pytest.mark.parametrize("mail, crossref", STRICT_PAIRS)
def test_alnum_key_matches_real_email_vs_crossref_pairs(mail, crossref):
    assert store.normalize_title(mail) != store.normalize_title(crossref)  # 기존 키로는 못 맞춘다
    assert cs.alnum_key(mail) == cs.alnum_key(crossref) != ""
    index = cs.TitleIndex()
    index.add_title(mail, "2026-10-20")
    assert index.lookup(crossref) == "2026-10-20"


def test_alnum_key_keeps_distinct_papers_apart():
    a = "Catalytic ammonia decomposition over ruthenium catalysts: Part I"
    b = "Catalytic ammonia decomposition over ruthenium catalysts: Part II"
    assert cs.alnum_key(a) != cs.alnum_key(b)
    assert cs.alnum_key("Short") == ""  # 너무 짧은 키는 쓰지 않는다
    assert cs.alnum_key("") == ""


def test_loose_match_is_not_used_by_lookup_but_by_lookup_loose():
    index = cs.TitleIndex()
    for mail, _ in LOOSE_PAIRS:
        index.add_title(mail, "2026-10-20")
    for mail, crossref in LOOSE_PAIRS:
        assert index.lookup(crossref) is None          # 수집 dedup 경로는 느슨한 일치를 쓰지 않는다
        assert index.lookup_loose(crossref) == "2026-10-20"
    assert cs.loose_match(cs.alnum_key("Part I of the long titled study on catalysts"),
                          cs.alnum_key("Part II of the long titled study on catalysts")) is True  # 그래서 수집에는 못 쓴다
    assert index.lookup_loose("Completely different paper about something else entirely") is None


def test_ingest_dedup_skips_rows_that_came_by_email_with_variant_titles(conn):
    mail_title = STRICT_PAIRS[3][0]
    add_row(conn, mail_title)
    add_row(conn, "Part I of the ruthenium ammonia decomposition study")
    items = [
        item("10.1038/s41467-026-1", STRICT_PAIRS[3][1], assertion=REVIEWED),
        item("10.1038/s41467-026-2", "Part II of the ruthenium ammonia decomposition study", assertion=REVIEWED),
        item("10.1038/s41467-026-3", "Part I of the ruthenium ammonia decomposition study", assertion=REVIEWED),
    ]
    index = cs.load_index(conn, [JOURNAL], global_exact=True, since_created="2026-01-01")
    part = cs.partition_items(items, JOURNAL, index, TODAY)
    assert part.in_db == [STRICT_PAIRS[3][1], "Part I of the ruthenium ammonia decomposition study"]
    assert [p.title for p in part.new] == ["Part II of the ruthenium ammonia decomposition study"]  # Part II 는 다른 논문


def test_load_index_alnum_uses_stored_title_for_entities(conn):
    add_row(conn, STRICT_PAIRS[2][0])  # DB 에 "&#xe5f8;" 가 글자 그대로 저장된 행
    index = cs.load_index(conn, [JOURNAL], global_exact=True, since_created=None)
    assert index.lookup(STRICT_PAIRS[2][1]) is not None


def test_shadow_compare_counts_loose_matches_in_matched_and_email_only(conn):
    mail_strict, cr_strict = STRICT_PAIRS[4]
    mail_loose, cr_loose = LOOSE_PAIRS[2]
    for t in (mail_strict, mail_loose, "Email only paper nowhere on crossref at all"):
        add_row(conn, t, created_at="2026-10-22T00:00:00Z")
    items = [
        item("10.1038/s41467-026-1", cr_strict, created="2026-10-20T00:00:00Z", assertion=REVIEWED),
        item("10.1038/s41467-026-2", cr_loose, created="2026-10-21T00:00:00Z", assertion=REVIEWED),
        item("10.1038/s41467-026-3", "Truly missing paper from the email feed", created="2026-10-21T00:00:00Z", assertion=REVIEWED),
    ]
    row = cf.shadow_compare(conn, JOURNAL, [JOURNAL], items, dt.date(2026, 10, 16), dt.date(2026, 10, 29))
    assert row["crossref_count"] == 3
    assert row["matched_in_db"] == 2 and row["loose_matched"] == 1
    assert row["missing_samples"] == ["Truly missing paper from the email feed"]
    assert row["email_only"] == 1 and row["loose_email_matched"] == 1  # 느슨한 쌍의 메일 행도 Crossref 에 있는 것으로 센다. 남는 email_only 는 진짜 메일 전용 행


def test_shadow_email_only_uses_loose_match_against_crossref_titles(conn):
    mail, cr = LOOSE_PAIRS[0]
    add_row(conn, mail, created_at="2026-10-22T00:00:00Z")
    # Crossref 항목이 구간 밖(여유분)이라 matched 에는 안 잡히고 email_only 판정에만 쓰인다
    items = [item("10.1038/s41467-026-1", cr, created="2026-10-01T00:00:00Z", assertion=REVIEWED)]
    row = cf.shadow_compare(conn, JOURNAL, [JOURNAL], items, dt.date(2026, 10, 16), dt.date(2026, 10, 29))
    assert row["email_only"] == 0 and row["loose_email_matched"] == 1


# ---------- 설정 ----------

def test_angewandte_queries_international_edition_issns():
    entry = next(e for e in cs.load_config()["journals"] if e["journal"] == "Angewandte Chemie")
    assert entry["issn"] == ["1433-7851", "1521-3773"]
    assert not any(e.get("alias_of") == "Angewandte Chemie" for e in cs.load_config()["journals"])
