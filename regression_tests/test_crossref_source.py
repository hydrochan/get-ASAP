"""crossref_source.py / crossref_fetch.py 회귀 테스트 - 합성 데이터와 가짜 HTTP만 사용한다(네트워크 없음).

cursor 쪽 넘김, 제목 정리(<sub>/<i>/엔티티), 발행일 선택과 미래 날짜 보정, 정정·update-to 제외, 정확/공백 무시 중복 판정,
드라이런 무변경, --apply 저장과 커서 전진, shadow 포착률 계산, 설정 파일 정합성을 검증한다.
"""
import datetime as dt
import io
import json
import re
import urllib.error
import urllib.parse
from pathlib import Path

import pytest

import crossref_fetch as cf
import crossref_source as cs
import store
from models import PaperMetadata

ROOT = Path(__file__).resolve().parent.parent
TODAY = dt.date(2026, 10, 29)
JOURNAL = "Synthetic Journal"


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(cs, "_today_utc", lambda: TODAY)
    monkeypatch.setattr(store, "_today", lambda: TODAY)
    monkeypatch.setattr(cs, "_sleep", lambda s: None)
    monkeypatch.delenv("CROSSREF_MAILTO", raising=False)
    cs.reset_stats()


def item(doi, title, created="2026-10-27T02:16:13Z", online="2026-10-27", **extra):
    d = {
        "DOI": doi,
        "title": [title],
        "type": "journal-article",
        "created": {"date-parts": [[int(x) for x in created[:10].split("-")]], "date-time": created},
    }
    if online:
        d["published-online"] = {"date-parts": [[int(p) for p in online.split("-")]]}
    d.update(extra)
    return d


def add_row(conn, title, journal=JOURNAL, created_at="2026-10-20T00:00:00Z"):
    store.save_papers([PaperMetadata(title=title, journal=journal, date="2026-10-20")], conn, today=TODAY)
    conn.execute("UPDATE papers SET created_at=? WHERE title=?", (created_at, title))


class FakeFetch:
    """cs.fetch_works 대역: ISSN 별로 준비한 항목을 돌려주고 호출 인자를 기록한다."""

    def __init__(self, by_issn):
        self.by_issn = by_issn
        self.calls = []
        self.many_calls = []

    def __call__(self, issn, from_created, until_created=None):
        self.calls.append((issn, str(from_created), None if until_created is None else str(until_created)))
        return list(self.by_issn.get(issn, []))

    def many(self, issns, from_created, until_created=None):
        """cs.fetch_works_many 대역: 항목마다 ISSN 목록을 붙여 돌려준다(실제 select 에 ISSN 이 들어 있다)."""
        self.many_calls.append((tuple(issns), str(from_created), None if until_created is None else str(until_created)))
        return [{**it, "ISSN": [issn]} for issn in issns for it in self.by_issn.get(issn, [])]


@pytest.fixture
def cfg(tmp_path):
    path = tmp_path / "crossref_journals.json"
    path.write_text(json.dumps({
        "skip_email_for_ingest": True,
        "journals": [
            {"journal": JOURNAL, "publisher": "synthetic", "issn": ["1111-2222", "3333-4444"], "mode": "ingest"},
            {"journal": "Shadow Journal", "publisher": "synthetic", "issn": ["5555-6666"], "mode": "shadow"},
            {"journal": "Shadow Alias", "publisher": "synthetic", "issn": ["5555-6666"], "mode": "shadow",
             "alias_of": "Shadow Journal"},
            {"journal": "Silent Journal", "publisher": "other", "issn": ["7777-8888"], "mode": "shadow"},
            {"journal": "No Issn Journal", "publisher": "other", "issn": [], "mode": "shadow", "note": "unresolved"},
        ],
    }), encoding="utf-8")
    return str(path)


# ---------- HTTP / 페이지 넘김 ----------

def test_fetch_works_follows_next_cursor_until_empty_page(monkeypatch):
    pages = {
        "*": ([item("10.1/a", "Alpha beta gamma delta one"), item("10.1/b", "Alpha beta gamma delta two")], "c1"),
        "c1": ([item("10.1/c", "Alpha beta gamma delta three")], "c2"),
        "c2": ([], "c3"),
    }
    urls = []

    def fake(url):
        urls.append(url)
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        items, nxt = pages[q["cursor"][0]]
        return {"message": {"items": items, "next-cursor": nxt}}

    monkeypatch.setattr(cs, "_get_json", fake)
    got = cs.fetch_works("2041-1723", "2026-10-01", "2026-10-27")

    assert [i["DOI"] for i in got] == ["10.1/a", "10.1/b", "10.1/c"]
    assert len(urls) == 3
    first = urllib.parse.urlsplit(urls[0])
    assert first.path == "/journals/2041-1723/works"
    q = urllib.parse.parse_qs(first.query)
    assert q["filter"] == ["type:journal-article,from-created-date:2026-10-01,until-created-date:2026-10-27"]
    assert q["rows"] == ["1000"] and q["cursor"] == ["*"]
    assert "mailto" not in q  # 환경변수가 없으면 붙이지 않는다


def test_fetch_works_stops_without_next_cursor_and_adds_mailto_only_from_env(monkeypatch):
    seen = []
    monkeypatch.setattr(cs, "_get_json", lambda url: seen.append(url) or {"message": {"items": [item("10.1/a", "x y z w v")]}})
    monkeypatch.setenv("CROSSREF_MAILTO", "ops@example.invalid")
    assert len(cs.fetch_works("2041-1723", "2026-10-01")) == 1
    assert len(seen) == 1
    q = urllib.parse.parse_qs(urllib.parse.urlsplit(seen[0]).query)
    assert q["mailto"] == ["ops@example.invalid"]
    assert "until-created-date" not in q["filter"][0]


def test_fetch_works_skips_the_empty_confirmation_page_when_total_results_is_reached(monkeypatch):
    seen = []
    page = {"message": {"items": [item("10.1/a", "x y z w v"), item("10.1/b", "x y z w u")], "next-cursor": "c1", "total-results": 2}}
    monkeypatch.setattr(cs, "_get_json", lambda url: seen.append(url) or page)
    assert len(cs.fetch_works("2041-1723", "2026-10-01")) == 2
    assert len(seen) == 1


class FakeResp:
    def __init__(self, body, headers=None):
        self._body = json.dumps(body).encode()
        self.headers = headers or {}

    def read(self, *a):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(code, headers=None):
    return urllib.error.HTTPError("https://api.crossref.org/x", code, "err", headers or {}, io.BytesIO(b""))


def test_get_json_retries_429_and_5xx_then_succeeds(monkeypatch):
    outcomes = [http_error(429, {"Retry-After": "2"}), http_error(503), FakeResp({"ok": 1}, {"X-Rate-Limit-Limit": "5", "X-Rate-Limit-Interval": "1s"})]
    sent = []
    sleeps = []

    def fake_urlopen(req, timeout=None):
        sent.append(req)
        out = outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(cs, "_sleep", sleeps.append)
    assert cs._get_json("https://api.crossref.org/x") == {"ok": 1}
    assert len(sent) == 3 and (cs.stats["requests"], cs.stats["retries"]) == (3, 2)
    assert sent[0].get_header("User-agent") == "get-ASAP (+https://get-asap.hydrochan.com)"
    assert sleeps[0] == 2.0 and sleeps[1] == 2.0  # Retry-After 와 지수 백오프(1->2) 중 큰 값
    assert sleeps[-1] == pytest.approx(0.2)  # 성공 뒤 interval/limit 만큼 쉰다


def test_get_json_gives_up_after_max_retries_and_does_not_retry_404(monkeypatch):
    calls = []

    def always(code):
        def fake_urlopen(req, timeout=None):
            calls.append(code)
            raise http_error(code)
        return fake_urlopen

    monkeypatch.setattr(cs.urllib.request, "urlopen", always(503))
    with pytest.raises(urllib.error.HTTPError):
        cs._get_json("https://api.crossref.org/x")
    assert len(calls) == cs.MAX_RETRIES + 1

    calls.clear()
    monkeypatch.setattr(cs.urllib.request, "urlopen", always(404))
    with pytest.raises(urllib.error.HTTPError):
        cs._get_json("https://api.crossref.org/x")
    assert calls == [404]


# ---------- 제목 / 날짜 ----------

@pytest.mark.parametrize("raw, expected", [
    ("Electroreduction of CO<sub>2</sub> to ethylene", "Electroreduction of CO2 to ethylene"),
    ("H<sub>2</sub>O<sub>2</sub> synthesis over Pd<i>x</i>Au catalysts", "H2O2 synthesis over PdxAu catalysts"),
    ("<jats:italic>Operando</jats:italic>\n   spectroscopy of  Fe&#8211;N sites", "Operando spectroscopy of Fe–N sites"),
    ("Cu &amp; Zn catalysts for CO<sup>2+</sup> uptake", "Cu & Zn catalysts for CO2+ uptake"),
    ("Strain effects when x < 5 and y > 3 in films", "Strain effects when x < 5 and y > 3 in films"),
    ("  Leading&nbsp;and trailing  ", "Leading and trailing"),
    ("CF\n  <sub>3</sub>\n  -substituted", "CF3-substituted"),
    ("Ti <sub>3</sub> XC <sub>2</sub>", "Ti3XC2"),
    ("Fe<sup>3+</sup> ions", "Fe3+ ions"),
    ("imaging\n <i>in vivo</i>", "imaging in vivo"),
    ("CO<sub>2</sub> reduction", "CO2 reduction"),
    ("CF\n <jats:sub>3</jats:sub>\n -substituted", "CF3-substituted"),
    ("Fe <MML:SUP>3+</MML:SUP> ions", "Fe3+ ions"),
    ("CO<sub>2</sub> Reduction - a review", "CO2 Reduction - a review"),
])
def test_clean_title(raw, expected):
    assert cs.clean_title(raw) == expected


def test_to_paper_builds_title_url_and_prefers_full_published_online():
    p = cs.to_paper(item("10.1038/s41467-026-1", "CO<sub>2</sub> capture in framework materials", online="2026-10-25"), "Nature Communications")
    assert isinstance(p, PaperMetadata)
    assert (p.title, p.journal, p.date, p.url) == (
        "CO2 capture in framework materials", "Nature Communications", "2026-10-25", "https://doi.org/10.1038/s41467-026-1"
    )


def test_to_paper_falls_back_to_created_date_when_published_is_partial_or_missing():
    base = dict(created="2026-10-27T23:50:00Z")
    partial = item("10.1/a", "Alpha beta gamma delta", **base)
    partial["published-online"] = {"date-parts": [[2026, 10]]}  # Elsevier/RSC: 연-월만
    assert cs.to_paper(partial, JOURNAL).date == "2026-10-27"  # created(UTC) 날짜
    assert cs.to_paper(item("10.1/b", "Alpha beta gamma delta", online=None, **base), JOURNAL).date == "2026-10-27"


def test_to_paper_clamps_future_dates_to_today_utc():
    assert cs.to_paper(item("10.1/a", "Alpha beta gamma delta", online="2026-12-31"), JOURNAL).date == "2026-10-29"
    assert cs.to_paper(item("10.1/b", "Alpha beta gamma delta", online="2026-10-29"), JOURNAL).date == "2026-10-29"
    assert cs.to_paper(item("10.1/c", "Alpha beta gamma delta", online="2026-10-28"), JOURNAL).date == "2026-10-28"
    assert cs.to_paper(item("10.1/d", "Alpha beta gamma delta", online="2026-12-31"), JOURNAL, today=dt.date(2027, 1, 5)).date == "2026-12-31"


def test_to_paper_rejects_items_without_doi_or_title_and_quotes_unsafe_doi_chars():
    assert cs.to_paper({"title": ["Alpha beta gamma delta"]}, JOURNAL) is None
    assert cs.to_paper({"DOI": "10.1/x", "title": []}, JOURNAL) is None
    p = cs.to_paper(item('10.1/a<b>"c', "Alpha beta gamma delta"), JOURNAL)
    assert "<" not in p.url and '"' not in p.url and p.url.startswith("https://doi.org/10.1/a")


@pytest.mark.parametrize("paper, mail", [
    ("2026-12-31", "2026-10-29"), ("2026-10-29", "2026-10-29"), ("2026-10-01", "2026-10-29"),
    ("", "2026-10-29"), ("2026-13-45", "2026-10-29"), ("2026-12-31T10:00:00Z", "2026-10-29"),
])
def test_effective_pub_date_is_equivalent_to_main(paper, mail):
    try:
        import main
    except Exception as e:  # google 라이브러리 등 main 의 의존성이 없는 환경
        pytest.skip(f"main import 불가: {e}")
    assert cs.effective_pub_date(paper, mail) == main._effective_pub_date(paper, mail)


# ---------- 비논문 제외 ----------

@pytest.mark.parametrize("title", [
    "Author Correction: Alpha beta gamma delta study",
    "Publisher Correction: Alpha beta gamma delta study",
    "Correction to Alpha beta gamma delta study",
    "Retraction Note: Alpha beta gamma delta study",
    "RETRACTED: Alpha beta gamma delta study",
    "Erratum: Alpha beta gamma delta study",
    "Corrigendum to Alpha beta gamma delta study",
    "Expression of Concern: Alpha beta gamma delta study",
    "Editorial: Alpha beta gamma delta study",
    "Addendum: Alpha beta gamma delta study",
    "Too short title",                       # parsers.filters 의 4단어 미만 규칙
    "Outside Front Cover: Alpha beta gamma",  # parsers.filters 의 prefix 규칙
])
def test_non_research_titles_are_rejected(title):
    assert cs.is_research_item(item("10.1/x", title), title) is False


@pytest.mark.parametrize("it, title, reason", [
    (item("10.1/x", "Alpha beta gamma delta study"), "Alpha beta gamma delta study", None),
    (item("10.1/x", "Alpha beta gamma delta study", **{"update-to": [{"DOI": "10.1/y"}]}), "Alpha beta gamma delta study", "update-to"),
    (item("10.1/x", "Alpha beta gamma delta study", type="book-chapter"), "Alpha beta gamma delta study", "type"),
    (item("10.1/x", "Author Correction: Alpha beta gamma"), "Author Correction: Alpha beta gamma", "correction-title"),
    (item("10.1/x", "Loss-engineered reconfigurable polaritons"), "Loss-engineered reconfigurable polaritons", "short-title"),
    (item("10.1/x", "Back cover"), "Back cover", "filters"),
    (item("10.1/x", "Front Cover: Alpha beta"), "Front Cover: Alpha beta", "filters"),
    # 라벨 접두 규칙(정규식 포함)에 걸린 짧은 제목은 'short-title' 이 아니라 'filters'
    (item("10.1/x", "Response to Comment"), "Response to Comment", "filters"),
    (item("10.1/x", "Preface: Alpha beta"), "Preface: Alpha beta", "filters"),
    (item("10.1/x", "Response to “Comment on Alpha beta”"), "Response to “Comment on Alpha beta”", "filters"),
])
def test_skip_reason_labels(it, title, reason):
    assert cs.skip_reason(it, title) == reason
    assert cs.is_research_item(it, title) is (reason is None)


def test_update_to_and_non_article_types_are_rejected_but_plain_research_passes():
    title = "Alpha beta gamma delta study of catalysts"
    assert cs.is_research_item(item("10.1/x", title), title) is True
    assert cs.is_research_item(item("10.1/x", title, **{"update-to": [{"DOI": "10.1/y", "type": "correction"}]}), title) is False
    assert cs.is_research_item(item("10.1/x", title, type="peer-review"), title) is False
    # 제목 중간의 'correction' 은 연구 논문일 수 있다(정정 판정은 제목 앞부분만)
    mid = "Quantum error correction with trapped ions in practice"
    assert cs.is_research_item(item("10.1/x", mid), mid) is True


# ---------- 중복 판정 ----------

def test_dedup_exact_and_whitespace_insensitive_with_window_and_journal_scope(conn):
    add_row(conn, "Alpha beta gamma delta study of catalysts")                         # 정확 일치용
    add_row(conn, "Carbon CO 2 capture in porous frameworks")                          # 파서가 쪼갠 "CO 2"
    add_row(conn, "Old CO 2 capture in ancient frameworks", created_at="2026-01-01T00:00:00Z")  # 180일보다 오래됨
    add_row(conn, "Other CO 2 capture in foreign frameworks", journal="Other Journal")  # 다른 저널
    add_row(conn, "Tombstoned paper that was purged earlier")
    conn.execute("UPDATE papers SET deleted_at='2026-10-21T00:00:00Z' WHERE title LIKE 'Tombstoned%'")

    index = cs.load_index(conn, [JOURNAL], global_exact=True, since_created="2026-05-02")
    items = [
        item("10.1/1", "Alpha  Beta Gamma Delta study of catalysts"),         # 정확(대소문자·공백 정리) 일치
        item("10.1/2", "Carbon CO<sub>2</sub> capture in porous frameworks"),  # 공백 무시 일치
        item("10.1/3", "Old CO2 capture in ancient frameworks"),               # 윈도 밖 -> 신규
        item("10.1/4", "Other CO2 capture in foreign frameworks"),             # 다른 저널 행과는 공백 무시 비교 안 함 -> 신규
        item("10.1/5", "Tombstoned paper that was purged earlier"),           # tombstone 도 중복
        item("10.1/6", "Brand new paper about nitrogen reduction", created="2026-10-28T10:00:00Z"),
        item("10.1/7", "Brand new paper about nitrogen reduction", created="2026-10-28T11:00:00Z"),  # 배치 안 중복
        item("10.1/8", "Author Correction: Alpha beta gamma delta study", created="2026-10-29T08:00:00Z"),
        item("10.1/9", "Another unrelated paper on perovskite films", created="2026-10-26T00:00:00Z", **{"update-to": [{"DOI": "10.1/z"}]}),
    ]
    part = cs.partition_items(items, JOURNAL, index, TODAY)

    assert [p.title for p in part.new] == [
        "Old CO2 capture in ancient frameworks",
        "Other CO2 capture in foreign frameworks",
        "Brand new paper about nitrogen reduction",
    ]
    assert len(part.in_db) == 3 and len(part.batch_dup) == 1
    assert sorted(r for r, _ in part.non_research) == ["correction-title", "update-to"]
    assert part.fetched == 9
    assert part.max_created == "2026-10-29T08:00:00Z"  # 건너뛴 항목 포함 created 최댓값


def test_exact_dedup_is_global_but_shadow_index_is_scoped_to_journal(conn):
    add_row(conn, "Shared title across two journals here", journal="Other Journal")
    assert cs.load_index(conn, [JOURNAL], global_exact=True, since_created=None).lookup("Shared title across two journals here")
    assert cs.load_index(conn, [JOURNAL], global_exact=False, since_created=None).lookup("Shared title across two journals here") is None


# ---------- 커서 / 설정 ----------

def test_cursor_only_moves_forward(conn):
    assert cs.get_cursor(conn, "1111-2222") is None
    cs.set_cursor(conn, "1111-2222", "2026-10-28T10:00:00Z")
    cs.set_cursor(conn, "1111-2222", "2026-10-20T00:00:00Z")  # 과거 값은 무시
    assert cs.get_cursor(conn, "1111-2222") == "2026-10-28T10:00:00Z"
    assert cs.cursor_from_date("2026-10-28T10:00:00Z") == "2026-10-26"  # 겹침 2일


def test_email_skip_journals_follows_flag_and_mode(cfg, tmp_path):
    assert cs.email_skip_journals(cfg) == {JOURNAL}
    off = tmp_path / "off.json"
    off.write_text(json.dumps({"skip_email_for_ingest": False, "journals": [{"journal": "A", "issn": ["1"], "mode": "ingest"}]}))
    assert cs.email_skip_journals(str(off)) == set()


def test_shipped_journals_file_is_consistent_with_publishers_json():
    config = cs.load_config()
    entries = config["journals"]
    publishers = json.loads((ROOT / "publishers.json").read_text(encoding="utf-8"))
    names = [e["journal"] for e in entries]

    assert len(names) == len(set(names))
    assert {j for p in publishers.values() for j in p["journals"]} <= set(names)  # publishers.json 의 모든 저널이 있다(이름 동일)
    assert config["skip_email_for_ingest"] is True
    assert cs.email_skip_journals() == {"Nature Communications", "iScience", "Physical Chemistry Chemical Physics"}
    assert {e["mode"] for e in entries} == {"ingest", "shadow"}
    by_name = {e["journal"]: e for e in entries}
    for e in entries:
        assert all(re.fullmatch(r"\d{4}-\d{3}[\dX]", i) for i in e["issn"]), e
        if e["mode"] == "ingest":
            assert e["issn"], e  # 수집 대상은 ISSN 이 있어야 한다
        if not e["issn"]:
            assert e.get("note"), e  # 미해결에는 사유
        if e.get("alias_of"):
            assert e["alias_of"] in by_name and by_name[e["alias_of"]]["issn"] == e["issn"]
    assert by_name["JACS"]["alias_of"] == "Journal of the American Chemical Society"
    assert by_name["Nature Communications"]["issn"][0] == "2041-1723"
    assert by_name["iScience"]["issn"][0] == "2589-0042"
    assert by_name["Physical Chemistry Chemical Physics"]["issn"][0] == "1463-9076"
    assert "Engineering Research from ACS Publications" not in by_name


def test_every_works_query_sends_a_narrow_select(monkeypatch):
    urls = []
    monkeypatch.setattr(cs, "_get_json", lambda url: urls.append(url) or {"message": {"items": []}})
    cs.fetch_works("2041-1723", "2026-10-01")
    cs.fetch_works_many(["1463-9076", "1463-9084", "2041-1723"], "2026-10-01", "2026-10-27")

    single, many = (urllib.parse.parse_qs(urllib.parse.urlsplit(u).query) for u in urls)
    assert single["select"] == ["DOI,title,type,created,published-online,update-to"]
    assert many["select"] == ["DOI,title,type,created,published-online,update-to,ISSN"]  # 묶음 조회만 ISSN 을 더 받는다
    for q in (single, many):
        assert not {"reference", "abstract", "author", "link", "license"} & set(q["select"][0].split(","))
        assert q["cursor"] == ["*"] and q["rows"] == ["1000"]
    assert urllib.parse.urlsplit(urls[1]).path == "/works"
    assert many["filter"] == [
        "type:journal-article,from-created-date:2026-10-01,until-created-date:2026-10-27,issn:1463-9076,issn:1463-9084,issn:2041-1723"
    ]


def test_get_json_uses_60s_timeout_and_records_pool_concurrency_limit(monkeypatch):
    seen = {}

    def fake_urlopen(req, timeout=None):
        seen["timeout"] = timeout
        return FakeResp({"ok": 1}, {"X-Concurrency-Limit": "1", "X-Api-Pool": "public-single"})

    monkeypatch.setattr(cs.urllib.request, "urlopen", fake_urlopen)
    assert cs.stats["concurrency_limit"] is None
    cs._get_json("https://api.crossref.org/x")
    assert seen["timeout"] == 60 and cs.stats["concurrency_limit"] == 1


def shadow_entries(n):
    return [{"journal": f"J{k}", "publisher": "p", "issn": [f"0000-000{k}", f"1000-000{k}"], "mode": "shadow"} for k in range(n)]


def test_fetch_shadow_caps_in_flight_requests_at_pool_limit_and_is_deterministic(monkeypatch):
    import threading
    import time

    def run(limit, workers, batch_size):
        state = {"now": 0, "max": 0, "calls": 0}
        lock = threading.Lock()

        def fake_many(issns, from_created, until_created=None):
            with lock:
                state["calls"] += 1
                n = state["calls"]
                if n == 1:
                    cs.stats["concurrency_limit"] = limit  # 첫 응답 헤더가 한도를 알려 준다
                state["now"] += 1
                state["max"] = max(state["max"], state["now"])
            time.sleep(0.02 - 0.004 * (n % 4))  # 완료 순서가 제출 순서와 달라지게
            with lock:
                state["now"] -= 1
            return [{"DOI": f"10.9/{i}", "title": [f"Paper from {i}"], "ISSN": [i]} for i in issns if i.startswith("0")]

        monkeypatch.setattr(cs, "fetch_works_many", fake_many)
        cs.stats["concurrency_limit"] = None
        out = cf.fetch_shadow(shadow_entries(8), dt.date(2026, 10, 2), dt.date(2026, 10, 29), workers, batch_size)
        return out, state

    public, st = run(limit=1, workers=4, batch_size=1)   # public pool: 동시 1건
    assert st["calls"] == 8 and st["max"] == 1
    polite, st = run(limit=3, workers=4, batch_size=1)   # polite pool: 요청이 4여도 3건까지
    assert 1 <= st["max"] <= 3
    again, _ = run(limit=3, workers=4, batch_size=1)
    assert list(public) == [f"J{k}" for k in range(8)] == list(polite)  # 저널명 순
    assert public == polite == again
    assert public["J3"] == ([{"DOI": "10.9/0000-0003", "title": ["Paper from 0000-0003"], "ISSN": ["0000-0003"]}], None)


def test_fetch_shadow_groups_batched_items_back_to_journals_and_isolates_a_failed_batch(monkeypatch):
    calls = []

    def fake_many(issns, from_created, until_created=None):
        calls.append(tuple(issns))
        if "0000-0002" in issns:
            raise urllib.error.URLError("down")
        return [
            # J0 의 인쇄·전자 ISSN 이 둘 다 달린 한 항목 -> 중복 없이 한 번
            {"DOI": "10.9/a", "title": ["Alpha beta gamma delta"], "ISSN": ["0000-0000", "1000-0000"]},
            {"DOI": "10.9/b", "title": ["Bravo beta gamma delta"], "ISSN": ["1000-0001"]},
            {"DOI": "10.9/z", "title": ["Not ours at all here"], "ISSN": ["9999-9999"]},
        ]

    monkeypatch.setattr(cs, "fetch_works_many", fake_many)
    out = cf.fetch_shadow(shadow_entries(3), dt.date(2026, 10, 2), dt.date(2026, 10, 29), 4, 2)

    assert calls == [("0000-0000", "0000-0001", "1000-0000", "1000-0001"), ("0000-0002", "1000-0002")]  # 2저널씩 묶음
    assert [i["DOI"] for i in out["J0"][0]] == ["10.9/a"] and out["J0"][1] is None
    assert [i["DOI"] for i in out["J1"][0]] == ["10.9/b"]
    assert out["J2"][0] is None and isinstance(out["J2"][1], urllib.error.URLError)  # 실패한 묶음의 저널만 오류


def test_shadow_logs_batch_timing_at_debug(conn, cfg, monkeypatch, tmp_path, caplog):
    monkeypatch.setattr(cf, "SHADOW_LOG", str(tmp_path / "s.jsonl"))
    monkeypatch.setattr(cs, "fetch_works_many", FakeFetch({}).many)
    caplog.set_level("DEBUG", logger="crossref_fetch")
    assert cf.main(["shadow", "--config", cfg, "--journals", "Shadow Journal", "--debug"]) == 0
    assert any("묶음" in r.message and r.levelname == "DEBUG" for r in caplog.records)


# ---------- ingest CLI ----------

def snapshot(conn):
    return (
        store.last_seq(conn),
        conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0],
        sorted(r[0] for r in conn.execute("SELECT key FROM meta")),
    )


def ingest_items():
    return [
        item("10.1/a", "Alpha beta gamma delta new paper one", created="2026-10-26T01:00:00Z", online="2026-10-26"),
        item("10.1/b", "Alpha beta gamma delta new paper two", created="2026-10-28T09:30:00Z", online="2026-10-28"),
        item("10.1/c", "Author Correction: Alpha beta gamma delta new", created="2026-10-28T20:00:00Z"),
        item("10.1/d", "Already stored paper about catalysis here", created="2026-10-27T00:00:00Z"),
    ]


def test_ingest_dry_run_makes_no_writes(conn, cfg, monkeypatch, capsys):
    add_row(conn, "Already stored paper about catalysis here")
    before = snapshot(conn)
    fake = FakeFetch({"1111-2222": ingest_items()})
    monkeypatch.setattr(cs, "fetch_works", fake)
    monkeypatch.setattr(cf.backup_db, "make_backup", lambda: pytest.fail("드라이런은 백업도 하지 않는다"))
    monkeypatch.setattr(cf.store, "export_csv", lambda *a, **k: pytest.fail("드라이런은 CSV 를 쓰지 않는다"))

    assert cf.main(["--config", cfg, "--since", "2026-09-07"]) == 0  # 모드 생략 = ingest

    assert snapshot(conn) == before
    assert fake.calls == [("1111-2222", "2026-09-07", None)]  # issn[0] 으로 조회
    out = capsys.readouterr().out
    assert "드라이런" in out and JOURNAL in out
    row = [ln for ln in out.splitlines() if ln.startswith(JOURNAL)][0].split()
    assert row[-5:] == ["4", "1", "1", "0", "2"]  # fetched / non_research / in_db / batch_dup / new
    assert "Shadow Journal" not in out  # shadow 저널은 ingest 대상이 아니다


def test_ingest_apply_saves_new_papers_advances_cursor_and_is_idempotent(conn, cfg, monkeypatch):
    add_row(conn, "Already stored paper about catalysis here")
    fake = FakeFetch({"1111-2222": ingest_items()})
    monkeypatch.setattr(cs, "fetch_works", fake)
    backups = []
    monkeypatch.setattr(cf.backup_db, "make_backup", lambda: backups.append(1) or "backup.db.gz")

    assert cf.main(["ingest", "--config", cfg, "--apply", "--since", "2026-10-20"]) == 0

    rows = {r["title"]: r for r in conn.execute("SELECT * FROM papers")}
    assert set(rows) == {
        "Already stored paper about catalysis here", "Alpha beta gamma delta new paper one", "Alpha beta gamma delta new paper two",
    }
    one = rows["Alpha beta gamma delta new paper one"]
    assert (one["journal"], one["pub_date"], one["bucket_month"], one["url"], one["status"], one["status_source"]) == (
        JOURNAL, "2026-10-26", "2026-10", "https://doi.org/10.1/a", "대기중", "get_asap"
    )
    assert backups == [1]  # 새 논문이 있어 백업 선행
    assert cs.get_cursor(conn, "1111-2222") == "2026-10-28T20:00:00Z"  # 건너뛴 정정 항목 포함 created 최댓값
    assert "Alpha beta gamma delta new paper one" in Path(store.csv_path("2026-10")).read_text(encoding="utf-8-sig")
    seq = store.last_seq(conn)

    # 두 번째 실행: 커서에서 2일 겹쳐 조회하고, 이미 있는 제목뿐이라 아무것도 쓰지 않으며 백업도 하지 않는다
    assert cf.main(["ingest", "--config", cfg, "--apply"]) == 0
    assert fake.calls[-1] == ("1111-2222", "2026-10-26", None)
    assert store.last_seq(conn) == seq and backups == [1]
    assert cs.get_cursor(conn, "1111-2222") == "2026-10-28T20:00:00Z"


def test_ingest_apply_skip_backup_and_journal_filter(conn, cfg, monkeypatch):
    monkeypatch.setattr(cs, "fetch_works", FakeFetch({"1111-2222": ingest_items()}))
    monkeypatch.setattr(cf.backup_db, "make_backup", lambda: pytest.fail("--skip-backup 이면 백업하지 않는다"))
    assert cf.main(["ingest", "--config", cfg, "--apply", "--skip-backup", "--journals", "synthetic journal"]) == 0
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3
    assert cf.main(["ingest", "--config", cfg, "--journals", "Shadow Journal"]) == 1  # shadow 저널은 ingest 대상이 아님


def test_ingest_since_overrides_cursor(conn, cfg, monkeypatch):
    cs.set_cursor(conn, "1111-2222", "2026-10-28T10:00:00Z")
    fake = FakeFetch({})
    monkeypatch.setattr(cs, "fetch_works", fake)
    assert cf.main(["ingest", "--config", cfg]) == 0
    assert cf.main(["ingest", "--config", cfg, "--since", "2026-09-01"]) == 0
    no_cursor = FakeFetch({})
    conn.execute("DELETE FROM meta WHERE key LIKE 'crossref_cursor:%'")
    monkeypatch.setattr(cs, "fetch_works", no_cursor)
    assert cf.main(["ingest", "--config", cfg]) == 0
    assert [c[1] for c in fake.calls] == ["2026-10-26", "2026-09-01"]
    assert no_cursor.calls[0][1] == "2026-09-03"  # 오늘(10-29) - 56일


def test_ingest_fetch_failure_keeps_cursor_and_reports_error(conn, cfg, monkeypatch):
    def boom(*a, **k):
        raise urllib.error.URLError("down")

    monkeypatch.setattr(cs, "fetch_works", boom)
    assert cf.main(["ingest", "--config", cfg, "--apply", "--skip-backup"]) == 1
    assert cs.get_cursor(conn, "1111-2222") is None


# ---------- shadow ----------

def shadow_fixture(conn):
    for title, created_at in [
        ("Alpha beta gamma delta study one", "2026-10-18T00:00:00Z"),
        ("Carbon CO 2 capture in porous frameworks", "2026-10-22T12:00:00Z"),   # 공백 쪼개짐
        ("Third paper on catalysts here", "2026-10-25T12:00:00Z"),
        ("Margin paper registered before window", "2026-10-20T00:00:00Z"),       # DOI 는 구간 앞에 등록
        ("Email only paper nowhere on crossref", "2026-10-21T00:00:00Z"),        # Crossref 에 없음
        ("Late alias named paper in window", "2026-10-23T00:00:00Z"),
    ]:
        add_row(conn, title, journal="Shadow Journal", created_at=created_at)
    conn.execute("UPDATE papers SET journal='Shadow Alias' WHERE title LIKE 'Late alias%'")
    add_row(conn, "Dormant paper outside the window entirely", journal="Shadow Journal", created_at="2026-06-01T00:00:00Z")


def shadow_items():
    return [
        item("10.2/1", "Alpha beta gamma delta study one", created="2026-10-17T00:00:00Z"),
        item("10.2/2", "Carbon CO<sub>2</sub> capture in porous frameworks", created="2026-10-20T12:00:00Z"),
        item("10.2/3", "Third paper on catalysts here", created="2026-10-25T00:00:00Z"),
        item("10.2/4", "Fourth paper missing from email feed", created="2026-10-26T00:00:00Z"),
        item("10.2/5", "Fifth paper also missing from email", created="2026-10-28T00:00:00Z"),
        item("10.2/6", "Author Correction: Alpha beta gamma delta study one", created="2026-10-27T00:00:00Z"),
        item("10.2/7", "Late alias named paper in window", created="2026-10-22T00:00:00Z"),
        item("10.2/8", "Margin paper registered before window", created="2026-10-10T00:00:00Z"),  # 구간 앞 여유분
    ]


def test_shadow_compare_coverage_lag_and_email_only(conn):
    shadow_fixture(conn)
    row = cf.shadow_compare(conn, "Shadow Journal", ["Shadow Journal", "Shadow Alias"], shadow_items(),
                            dt.date(2026, 10, 16), dt.date(2026, 10, 29))

    assert row["crossref_count"] == 6            # 구간 안 연구 논문: 1,2,3,4,5,7 (정정 6 제외, 구간 앞 8 제외)
    assert row["matched_in_db"] == 4             # 1, 2(공백 무시), 3, 7(별칭 이름 행)
    assert row["missing_in_db"] == 2
    assert row["coverage_pct"] == pytest.approx(66.7)
    assert row["non_research_skipped"] == 1
    assert row["median_lag_days"] == pytest.approx(1.0)  # 지연 [1.0, 2.0, 0.5, 1.0]일의 중앙값
    # 도착 지연은 DB 행 기준: 1.0, 2.0, 0.5, 10.0(여유분 항목과 일치한 Margin 행), 1.0 (메일 전용 행은 제외) -> 중앙값 1.0
    assert row["median_arrival_lag_days"] == pytest.approx(1.0)
    assert row["db_rows_in_window"] == 6          # 구간 안 정본 행(휴면 행 제외)
    assert row["email_only"] == 1                 # 'Email only ...' 만. Margin 행은 여유분 Crossref 항목과 일치
    assert row["baseline_active"] is True
    assert row["missing_samples"] == ["Fourth paper missing from email feed", "Fifth paper also missing from email"]


def test_shadow_cli_is_read_only_logs_one_json_line_and_excludes_silent_journals(conn, cfg, monkeypatch, tmp_path, capsys):
    shadow_fixture(conn)
    log = tmp_path / "logs" / "crossref_shadow.jsonl"
    monkeypatch.setattr(cf, "SHADOW_LOG", str(log))
    fake = FakeFetch({"5555-6666": shadow_items(), "7777-8888": [item("10.3/1", "Silent journal paper about something", created="2026-10-20T00:00:00Z")]})
    monkeypatch.setattr(cs, "fetch_works_many", fake.many)
    monkeypatch.setattr(cf.backup_db, "make_backup", lambda: pytest.fail("shadow 는 백업·쓰기를 하지 않는다"))
    before = snapshot(conn)

    assert cf.main(["shadow", "--config", cfg, "--days", "14", "--until", "2026-10-29"]) == 0

    assert snapshot(conn) == before
    # 두 저널이 한 질의로 묶인다(요청 수 절감). 구간 앞 여유 14일
    assert fake.many_calls == [(("5555-6666", "7777-8888"), "2026-10-02", "2026-10-29")]
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["mode"] == "shadow" and rec["window"] == {"from": "2026-10-16", "to": "2026-10-29", "days": 14, "margin_days": 14}
    by_name = {j["journal"]: j for j in rec["journals"]}
    assert [j["journal"] for j in rec["journals"]] == ["Shadow Journal", "Silent Journal"]  # 저널명 순(결정적)
    assert set(by_name) == {"Shadow Journal", "Silent Journal"}  # 별칭·ISSN 없는 저널·ingest 저널은 조회 대상이 아니다
    assert by_name["Shadow Journal"]["coverage_pct"] == pytest.approx(66.7)
    assert by_name["Silent Journal"]["baseline_active"] is False
    assert rec["overall"]["crossref_count"] == 6 and rec["overall"]["matched_in_db"] == 4  # 기준선 없는 저널은 제외
    assert rec["by_publisher"]["synthetic"]["coverage_pct"] == pytest.approx(66.7)
    assert "메일 기준선 없음" in capsys.readouterr().out

    cf.main(["shadow", "--config", cfg, "--until", "2026-10-29", "--journals", "Shadow Journal"])
    assert len(log.read_text(encoding="utf-8").splitlines()) == 2  # 실행마다 한 줄 덧붙인다


def test_shadow_default_window_ends_three_days_ago(conn, cfg, monkeypatch, tmp_path):
    monkeypatch.setattr(cf, "SHADOW_LOG", str(tmp_path / "s.jsonl"))
    fake = FakeFetch({})
    monkeypatch.setattr(cs, "fetch_works_many", fake.many)
    assert cf.main(["shadow", "--config", cfg, "--journals", "Shadow Journal"]) == 0
    assert fake.many_calls == [(("5555-6666",), "2026-09-29", "2026-10-26")]  # 끝=10-29-3일, 시작=끝-13일, 여유 14일
    assert cf.main(["shadow", "--config", cfg, "--journals", "Shadow Journal", "--margin-days", "60"]) == 0
    assert fake.many_calls[-1] == (("5555-6666",), "2026-08-14", "2026-10-26")


def test_missing_db_fails_cleanly(monkeypatch, tmp_path, capsys, cfg):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "missing" / "asap.db"))
    assert cf.main(["shadow", "--config", cfg]) == 1
    assert "정본 DB가 없습니다" in capsys.readouterr().err
    assert not (tmp_path / "missing").exists()
