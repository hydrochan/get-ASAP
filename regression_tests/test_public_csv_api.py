"""공개 슬림 CSV / 버전 목록 / 원문 링크 리다이렉트 회귀 테스트 (store.py export + dashboard/server.py).

  GET /api/versions                       -> {"months": {"2026-11": "<ver>", ...}}, Cache-Control: no-cache
  GET /api/csv/<월>?fmt=slim[&v=<ver>]    -> id,title,journal,date,doi,gpt_reason
  GET /api/link/<id>                      -> 302 Location: 저장된 url

합성 데이터만 쓰며 실제 .env, data/asap.db, cache/ 는 건드리지 않는다.
"""
import csv
import datetime as dt
import http.client
import http.server
import io
import json
import os
import pathlib
import sqlite3
import threading
import urllib.parse
from types import SimpleNamespace

import pytest

import store
from models import PaperMetadata

SLIM_HEADER = ["id", "title", "journal", "date", "doi", "gpt_reason"]
FULL_HEADER = ["title", "journal", "date", "url", "status", "gpt_reason"]


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch):
    monkeypatch.setattr(store, "_today", lambda: dt.date(2026, 11, 15))


@pytest.fixture
def api(db_file, monkeypatch, tmp_path):
    import server

    monkeypatch.setattr(server, "CACHE_DIR", str(tmp_path / "cache"))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(path, headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers=headers or {})
        resp = c.getresponse()
        data = resp.read()
        c.close()
        return SimpleNamespace(status=resp.status, headers={k.lower(): v for k, v in resp.getheaders()}, data=data)

    yield SimpleNamespace(get=get, cache=tmp_path / "cache", db_file=db_file)
    httpd.shutdown()
    httpd.server_close()


def paper(title, date="2026-09-07", url="https://example.invalid/p", journal="Synthetic Journal"):
    return PaperMetadata(title=title, journal=journal, date=date, url=url)


def parse(data: bytes):
    assert data.startswith(b"\xef\xbb\xbf")  # 기존 CSV 와 같은 BOM 규약
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))


def set_reason(conn, title, reason):
    ref = conn.execute("SELECT ref FROM papers WHERE title=?", (title,)).fetchone()["ref"]
    store.apply_verdicts(conn, [{"ref": ref, "status": "관련", "gpt_reason": reason}], 0)


def paper_id(conn, title):
    return conn.execute("SELECT id FROM papers WHERE title=?", (title,)).fetchone()["id"]


# ---------- DOI 추출 ----------

@pytest.mark.parametrize(
    "url, expected",
    [
        ("https://doi.org/10.1021/acs.chemmater.5c01234", "10.1021/acs.chemmater.5c01234"),
        ("https://dx.doi.org/10.1002/adma.202512345?utm_source=email#abs", "10.1002/adma.202512345"),
        # 추적 리다이렉트 링크에 DOI 가 없으면 빈 문자열(Wiley / Elsevier / ACS SendGrid)
        ("https://click.wiley.com/ls/click?upn=u001.Ab12-2BxyZ-2FqQ", ""),
        ("https://www.sciencedirect.com/science/article/pii/S0378775326001234?dgcid=raven_sd_via_email", ""),
        ("https://u12345.ct.sendgrid.net/ls/click?upn=u001.AbCdEf", ""),
        # 한 번 / 두 번 인코딩된 DOI
        ("https://r.invalid/x?u=https%3A%2F%2Fdoi.org%2F10.1002%2Fadma.202512345%3Futm%3Dx", "10.1002/adma.202512345"),
        ("https://r.invalid/x?u=https%253A%252F%252Fdoi.org%252F10.1039%252Fd5ee00123a", "10.1039/d5ee00123a"),
        # 끝의 . 와 ) 제거 / 구분자(? & # 공백 따옴표)에서 끊김
        ("https://doi.org/10.1016/j.jpowsour.2026.123456.", "10.1016/j.jpowsour.2026.123456"),
        ("https://r.invalid/?a=(10.1016/j.nanoen.2026.109876)", "10.1016/j.nanoen.2026.109876"),
        ("https://r.invalid/?doi=10.1038/s41560-026-01234-5&x=1", "10.1038/s41560-026-01234-5"),
        # 등록기관 번호는 4~9자리
        ("https://r.invalid/10.12/abc", ""),
        ("", ""),
        (None, ""),
    ],
)
def test_doi_from_url(url, expected):
    assert store.doi_from_url(url) == expected


# ---------- export: 슬림 CSV ----------

def test_slim_path_is_not_matched_by_papers_glob():
    name = os.path.basename(store.slim_csv_path("2026-09", "x"))
    assert not name.startswith("papers_")  # migrate_from_notion 게이트의 papers_*.csv 훑기에 걸리지 않는다


def test_export_writes_slim_with_same_rows_order_and_filter_as_full(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(store, "is_excluded_journal", lambda j: j == "Excluded Journal")
    store.save_papers(
        [
            paper("Late", date="2026-09-20", url="https://doi.org/10.1021/late.1"),
            paper("Early", date="2026-09-02", url="https://click.wiley.com/ls/click?upn=u001.tracking-secret-token"),
            paper("Out", date="2026-09-10", journal="Excluded Journal"),
            paper("Gone", date="2026-09-11"),
            paper("Twin", date="2026-09-12", url="https://r.invalid/x?u=https%3A%2F%2Fdoi.org%2F10.1002%2Ftwin.2%3Fz"),
        ],
        conn,
    )
    set_reason(conn, "Early", 'reason, with "quotes"\nand newline')
    conn.execute("UPDATE papers SET deleted_at='2026-09-30T00:00:00Z' WHERE title='Gone'")
    cache = str(tmp_path / "out")
    assert store.export_csv("2026-09", conn, cache) == 3

    full = parse(pathlib.Path(store.csv_path("2026-09", cache)).read_bytes())
    raw_slim = pathlib.Path(store.slim_csv_path("2026-09", cache)).read_bytes()
    slim = parse(raw_slim)

    assert full[0] == FULL_HEADER and slim[0] == SLIM_HEADER
    assert raw_slim.startswith(b"\xef\xbb\xbf" + b'"id","title","journal","date","doi","gpt_reason"\n')
    assert b"\r" not in raw_slim  # 줄바꿈은 OS 와 무관하게 \n 고정
    # 같은 행·순서(발행일 오름차순), 제외 저널·tombstone 제외
    assert [r[0] for r in full[1:]] == [r[1] for r in slim[1:]] == ["Early", "Twin", "Late"]
    assert [r[1] for r in full[1:]] == [r[2] for r in slim[1:]]  # journal
    assert [r[2] for r in full[1:]] == [r[3] for r in slim[1:]] == ["2026-09-02", "2026-09-12", "2026-09-20"]  # date
    assert [r[5] for r in full[1:]] == [r[5] for r in slim[1:]]  # gpt_reason
    assert slim[1][5] == 'reason, with "quotes"\nand newline'
    # id = papers.id, doi 는 url 에서 파생, url·status 는 없음
    assert [int(r[0]) for r in slim[1:]] == [paper_id(conn, t) for t in ("Early", "Twin", "Late")]
    assert [r[4] for r in slim[1:]] == ["", "10.1002/twin.2", "10.1021/late.1"]
    assert b"tracking-secret-token" not in raw_slim and b"example.invalid" not in raw_slim
    assert os.listdir(cache).count(".export.lock") == 1 and not [n for n in os.listdir(cache) if n.endswith(".tmp")]


def test_export_dirty_backfills_missing_slim_file(conn, tmp_path):
    cache = str(tmp_path / "cache")
    store.save_papers([paper("A", date="2026-09-01")], conn)
    assert store.export_dirty(conn, cache) == {"2026-09": 1}
    assert store.export_dirty(conn, cache) == {}
    os.remove(store.slim_csv_path("2026-09", cache))  # 슬림 도입 전에 export 된 배포 상태
    assert store.export_dirty(conn, cache) == {"2026-09": 1}
    assert os.path.exists(store.slim_csv_path("2026-09", cache))


# ---------- 버전 ----------

def test_versions_endpoint_headers_and_months(api, conn):
    store.save_papers([paper("S", date="2026-09-01"), paper("O", date="2026-10-02")], conn)
    store.export_all(conn, str(api.cache))
    # 범위 밖 월 파일(첫 버킷 월 이전·이번 달 이후)과 무관한 파일은 목록에 없다
    for name in ("papers_2026-03.csv", "papers_2026-12.csv", "papers_notamonth.csv"):
        (api.cache / name).write_bytes(b"\xef\xbb\xbf")
    r = api.get("/api/versions")
    assert r.status == 200
    assert r.headers["cache-control"] == "no-cache"
    assert r.headers["content-type"].startswith("application/json")
    months = json.loads(r.data)["months"]
    assert list(months) == ["2026-10", "2026-09"]  # 최신 월 먼저
    assert all(isinstance(v, str) and v for v in months.values()) and months["2026-10"] != months["2026-09"]
    assert json.loads(api.get("/api/versions").data)["months"] == months  # 변경 없으면 같은 값


def test_version_changes_only_when_slim_content_changes(api, conn):
    store.save_papers([paper("S", date="2026-09-01"), paper("O", date="2026-10-02")], conn)
    store.export_all(conn, str(api.cache))
    before = json.loads(api.get("/api/versions").data)["months"]

    store.export_all(conn, str(api.cache))  # 내용이 같은 재export(예: refresh_csv --all) -> 버전 그대로
    assert json.loads(api.get("/api/versions").data)["months"] == before

    set_reason(conn, "S", "new reason")
    assert store.export_dirty(conn, str(api.cache)) == {"2026-09": 1}
    after = json.loads(api.get("/api/versions").data)["months"]
    assert after["2026-09"] != before["2026-09"]
    assert after["2026-10"] == before["2026-10"]  # 안 바뀐 월은 그대로


def test_version_changes_when_content_changes_without_csv_seq_change(api, conn, monkeypatch):
    """제외 저널 목록 변경 후 refresh_csv --all: csv_seq 는 그대로인데 내용이 바뀐다 -> 버전도 바뀌어야 한다."""
    store.save_papers([paper("Keep", date="2026-09-01"), paper("Drop", date="2026-09-02", journal="Later Excluded")], conn)
    store.export_all(conn, str(api.cache))
    before = json.loads(api.get("/api/versions").data)["months"]["2026-09"]
    seq = conn.execute("SELECT value FROM meta WHERE key='csv_seq:2026-09'").fetchone()[0]
    monkeypatch.setattr(store, "is_excluded_journal", lambda j: j == "Later Excluded")
    store.export_all(conn, str(api.cache))
    assert conn.execute("SELECT value FROM meta WHERE key='csv_seq:2026-09'").fetchone()[0] == seq
    assert json.loads(api.get("/api/versions").data)["months"]["2026-09"] != before


# ---------- /api/csv ?fmt=slim ----------

def test_slim_endpoint_body_headers_and_cache_control(api, conn):
    store.save_papers([paper("S", date="2026-09-01", url="https://doi.org/10.1021/s.1")], conn)
    store.export_all(conn, str(api.cache))
    ver = json.loads(api.get("/api/versions").data)["months"]["2026-09"]

    plain = api.get("/api/csv/2026-09?fmt=slim")
    assert plain.status == 200 and plain.headers["content-type"] == "text/csv; charset=utf-8"
    assert plain.headers["cache-control"] == "public, max-age=7200"  # v 없으면 기존 그대로
    rows = parse(plain.data)
    assert rows[0] == SLIM_HEADER and rows[1][1:] == ["S", "Synthetic Journal", "2026-09-01", "10.1021/s.1", ""]
    assert int(plain.headers["content-length"]) == len(plain.data)

    versioned = api.get(f"/api/csv/2026-09?fmt=slim&v={ver}")
    assert versioned.status == 200 and versioned.data == plain.data
    assert versioned.headers["cache-control"] == "public, max-age=31536000, immutable"

    stale = api.get("/api/csv/2026-09?fmt=slim&v=0000-stale")  # 옛 v 도 404 가 아니라 현재 내용
    assert stale.status == 200 and stale.data == plain.data
    assert stale.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert api.get("/api/csv/2026-09?fmt=slim&v=").headers["cache-control"] == "public, max-age=7200"  # 빈 v 는 없는 것


def test_slim_endpoint_errors(api, conn):
    store.save_papers([paper("S", date="2026-09-01")], conn)
    store.export_all(conn, str(api.cache))
    assert api.get("/api/csv/2026-08?fmt=slim&v=x").status == 404  # 전체 CSV 도 없는 달
    assert api.get("/api/csv/2026-08").status == 404
    assert api.get("/api/csv/abc?fmt=slim").status == 400
    assert api.get("/api/csv/..%2Fx?fmt=slim").status in (400, 404)
    assert api.get("/api/csv/2026-09?fmt=bogus").status == 400


def test_slim_is_generated_lazily_when_missing_or_older_than_full(api, conn):
    store.save_papers([paper("S", date="2026-09-01")], conn)
    store.export_all(conn, str(api.cache))
    slim = store.slim_csv_path("2026-09", str(api.cache))
    good = pathlib.Path(slim).read_bytes()

    os.remove(slim)  # 배포 직후: 전체 CSV 만 있다
    r = api.get("/api/csv/2026-09?fmt=slim")
    assert r.status == 200 and r.data == good and os.path.exists(slim)
    assert json.loads(api.get("/api/versions").data)["months"]["2026-09"]

    pathlib.Path(slim).write_bytes(b"junk")  # 전체 CSV 가 슬림보다 새로우면(수동 교체 등) 다시 만든다
    full = store.csv_path("2026-09", str(api.cache))
    newer = os.stat(slim).st_mtime + 100
    os.utime(full, (newer, newer))
    assert api.get("/api/csv/2026-09?fmt=slim").data == good

    os.remove(slim)
    os.remove(api.cache / "papers_2026-09.csv")  # 전체 CSV 가 없으면 슬림 요청도 404 (기존 경로와 동일)
    assert api.get("/api/csv/2026-09?fmt=slim").status == 404


def test_slim_without_db_is_503_but_existing_slim_still_served(api, conn, monkeypatch, tmp_path):
    store.save_papers([paper("S", date="2026-09-01")], conn)
    store.export_all(conn, str(api.cache))
    slim = store.slim_csv_path("2026-09", str(api.cache))
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "ghost" / "asap.db"))
    assert api.get("/api/csv/2026-09?fmt=slim").status == 200  # 이미 있는 슬림은 DB 없이도
    os.remove(slim)
    r = api.get("/api/csv/2026-09?fmt=slim")
    assert r.status == 503 and r.headers["cache-control"] == "no-store"
    assert json.loads(api.get("/api/versions").data) == {"months": {}}
    assert not (tmp_path / "ghost").exists()


# ---------- 기존 전체 CSV 는 그대로 ----------

def test_full_csv_endpoint_is_unchanged(api, conn):
    store.save_papers([paper("S", date="2026-09-01", url="https://doi.org/10.1021/s.1")], conn)
    set_reason(conn, "S", "why")
    store.export_all(conn, str(api.cache))
    for qs in ("", "?v=abc", "?fmt="):  # fmt/v 가 없거나 비어 있으면 기존 응답
        r = api.get("/api/csv/2026-09" + qs)
        assert r.status == 200
        assert r.headers["cache-control"] == "public, max-age=7200"
        rows = parse(r.data)
        assert rows[0] == ["title", "journal", "date", "url", "gpt_reason"]  # status 는 서빙 시 제거(기존 동작)
        assert rows[1] == ["S", "Synthetic Journal", "2026-09-01", "https://doi.org/10.1021/s.1", "why"]
    # 디스크의 전체 CSV 형식도 그대로(6열)
    assert parse(pathlib.Path(store.csv_path("2026-09", str(api.cache))).read_bytes())[0] == FULL_HEADER


# ---------- AI 비논문·논평 숨김 (ai_article_kind) ----------

def set_kind(conn, title, kind):
    ref = conn.execute("SELECT ref FROM papers WHERE title=?", (title,)).fetchone()["ref"]
    return store.apply_verdicts(conn, [{"ref": ref, "article_kind": kind}], 0)["results"][0]["result"]


def titles_in(path):
    return [r[0] for r in parse(pathlib.Path(path).read_bytes())[1:]]


def slim_titles_in(path):
    return [r[1] for r in parse(pathlib.Path(path).read_bytes())[1:]]


KIND_PAPERS = [
    paper("Real research", date="2026-09-01"),
    paper("News item", date="2026-09-02"),
    paper("Comment on X", date="2026-09-03"),
    paper("A review", date="2026-09-04"),
    paper("Unmarked", date="2026-09-05"),
    paper("Marked research", date="2026-09-06"),
]


def seed_kinds(conn):
    store.save_papers(KIND_PAPERS, conn)
    assert [set_kind(conn, t, k) for t, k in (
        ("News item", "non_research"), ("Comment on X", "comment"), ("A review", "review"), ("Marked research", "research"),
    )] == ["updated"] * 4


def test_hidden_kinds_are_dropped_from_full_and_slim_csv_but_rows_stay_in_db(conn, tmp_path, caplog):
    seed_kinds(conn)
    cache = str(tmp_path / "out")
    with caplog.at_level("INFO", logger="store"):
        assert store.export_csv("2026-09", conn, cache) == 4
    # 비논문·논평만 빠지고 리뷰·연구·미판정은 그대로(순서 유지)
    assert titles_in(store.csv_path("2026-09", cache)) == ["Real research", "A review", "Unmarked", "Marked research"]
    assert slim_titles_in(store.slim_csv_path("2026-09", cache)) == ["Real research", "A review", "Unmarked", "Marked research"]
    assert "AI 비논문·논평 2건 CSV 캐시에서 숨김 (2026-09)" in caplog.text
    assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 6  # 지우지 않는다
    assert conn.execute("SELECT COUNT(*) FROM papers WHERE deleted_at IS NOT NULL").fetchone()[0] == 0
    # 열 형식은 그대로(글 종류 열이 공개 CSV 로 새지 않는다)
    assert parse(pathlib.Path(store.csv_path("2026-09", cache)).read_bytes())[0] == FULL_HEADER
    assert parse(pathlib.Path(store.slim_csv_path("2026-09", cache)).read_bytes())[0] == SLIM_HEADER


def test_hidden_kinds_do_not_appear_on_public_endpoints(api, conn):
    seed_kinds(conn)
    store.export_all(conn, str(api.cache))
    full = api.get("/api/csv/2026-09")
    slim = api.get("/api/csv/2026-09?fmt=slim")
    assert [r[0] for r in parse(full.data)[1:]] == ["Real research", "A review", "Unmarked", "Marked research"]
    assert [r[1] for r in parse(slim.data)[1:]] == ["Real research", "A review", "Unmarked", "Marked research"]
    assert b"News item" not in full.data + slim.data and b"Comment on X" not in full.data + slim.data
    # 슬림 지연 생성 경로(전체 CSV 만 있는 배포 직후)도 같은 필터를 쓴다
    os.remove(store.slim_csv_path("2026-09", str(api.cache)))
    assert b"News item" not in api.get("/api/csv/2026-09?fmt=slim").data


def test_hiding_and_restoring_regenerate_csv_and_change_version_without_seq_change(api, conn):
    cache = str(api.cache)
    store.save_papers(KIND_PAPERS[:3] + [paper("Other month", date="2026-10-05")], conn)
    store.export_all(conn, cache)
    seq0 = store.last_seq(conn)
    v0 = json.loads(api.get("/api/versions").data)["months"]
    assert store.export_dirty(conn, cache) == {}  # 아무 변화도 없으면 건드리지 않는다

    assert set_kind(conn, "News item", "non_research") == "updated"
    assert store.last_seq(conn) == seq0  # 피드/seq 불변 - csv_seq 메모 삭제만으로 dirty 가 된다
    assert store.export_dirty(conn, cache) == {"2026-09": 2}  # 해당 월만, 슬림·전체 모두 다시 씀
    assert titles_in(store.csv_path("2026-09", cache)) == ["Real research", "Comment on X"]
    assert slim_titles_in(store.slim_csv_path("2026-09", cache)) == ["Real research", "Comment on X"]
    v1 = json.loads(api.get("/api/versions").data)["months"]
    assert v1["2026-09"] != v0["2026-09"] and v1["2026-10"] == v0["2026-10"]  # 슬림 내용 해시가 바뀐다 / 다른 달은 그대로
    assert store.export_dirty(conn, cache) == {}

    # 숨김 여부가 안 바뀌는 표시(리뷰/연구)는 CSV 를 다시 쓰지 않는다
    assert set_kind(conn, "Real research", "review") == "updated"
    assert store.export_dirty(conn, cache) == {}

    # 같은 값 재전송은 unchanged 이고 아무것도 dirty 로 만들지 않는다
    assert set_kind(conn, "News item", "non_research") == "unchanged"
    assert store.export_dirty(conn, cache) == {}

    # 복원(AI 가 research 로 바꾸거나 관리자가 복원)하면 다시 나타난다
    assert set_kind(conn, "News item", "research") == "updated"
    assert store.export_dirty(conn, cache) == {"2026-09": 3}
    assert "News item" in titles_in(store.csv_path("2026-09", cache))
    assert json.loads(api.get("/api/versions").data)["months"]["2026-09"] != v1["2026-09"]
    assert set_kind(conn, "Comment on X", "comment") == "updated"
    assert store.export_dirty(conn, cache) == {"2026-09": 2}
    ref = conn.execute("SELECT ref FROM papers WHERE title='Comment on X'").fetchone()["ref"]
    store.set_human_article_kind(conn, [{"ref": ref, "kind": "research"}], "pytest-admin")   # 관리자 복원
    assert store.last_seq(conn) == seq0
    assert store.export_dirty(conn, cache) == {"2026-09": 3}
    assert titles_in(store.csv_path("2026-09", cache)) == ["Real research", "News item", "Comment on X"]


def test_hidden_row_stays_out_of_the_change_feed_and_admin_review_still_sees_it(conn):
    store.save_papers([paper("Feed row")], conn)
    cursor = store.last_seq(conn)
    set_kind(conn, "Feed row", "non_research")
    assert store.fetch_changes(conn, cursor)["rows"] == []  # 피드로 되돌아가지 않는다
    feed_row = store.fetch_changes(conn, 0)["rows"][0]
    assert feed_row["title"] == "Feed row" and not any("kind" in k for k in feed_row)
    review = store.list_review_papers(conn, "all")
    assert [r["title"] for r in review["rows"]] == ["Feed row"]  # 관리자 목록에는 숨긴 행도 나온다
    assert review["rows"][0]["ai_article_kind"] == "non_research"


# ---------- /api/link ----------

def test_link_redirects_to_stored_url(api, conn):
    url = "https://www.sciencedirect.com/science/article/pii/S1?dgcid=a&b=c%20d#frag"
    store.save_papers([paper("L", url=url)], conn)
    r = api.get(f"/api/link/{paper_id(conn, 'L')}")
    assert r.status == 302
    assert r.headers["location"] == url
    assert r.headers["cache-control"] == "public, max-age=86400"
    assert r.data == b""


def test_link_404_cases_leak_nothing(api, conn):
    store.save_papers(
        [paper("Live"), paper("Deleted"), paper("Mail", url="mailto:a@b.invalid"), paper("NoUrl", url="")], conn
    )
    conn.execute("UPDATE papers SET deleted_at='2026-09-30T00:00:00Z' WHERE title='Deleted'")
    ids = {t: paper_id(conn, t) for t in ("Live", "Deleted", "Mail", "NoUrl")}
    assert api.get(f"/api/link/{ids['Live']}").status == 302
    bad = [
        str(ids["Deleted"]), str(ids["Mail"]), str(ids["NoUrl"]),  # 삭제 / http 아님 / url 없음
        "99999", "0",                                              # 없는 id
        "abc", "1.5", "-1", "+1", "1e3", "0x1", "1%20", "", "1/2", "٣", "9" * 40,  # 정수 아님
    ]
    for raw in bad:
        r = api.get("/api/link/" + urllib.parse.quote(raw, safe=""))
        assert r.status == 404, raw
        assert r.data == b"" and "location" not in r.headers, raw


def test_link_encodes_non_ascii_and_blocks_header_injection(api, conn):
    store.save_papers([paper("Uni", url="https://example.invalid/논문?q=1"), paper("Inj")], conn)
    conn.execute(
        "UPDATE papers SET url=? WHERE title='Inj'", ("https://example.invalid/a\r\nSet-Cookie: pwn=1\r\n\r\nx",)
    )
    r = api.get(f"/api/link/{paper_id(conn, 'Uni')}")
    assert r.status == 302
    assert r.headers["location"] == "https://example.invalid/%EB%85%BC%EB%AC%B8?q=1"
    r = api.get(f"/api/link/{paper_id(conn, 'Inj')}")
    assert r.status == 302 and "set-cookie" not in r.headers
    assert "\r" not in r.headers["location"] and "\n" not in r.headers["location"]


def test_link_db_unavailable_is_503_and_db_is_not_created(api, monkeypatch, tmp_path):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "ghost" / "asap.db"))
    r = api.get("/api/link/1")
    assert r.status == 503
    assert not (tmp_path / "ghost").exists()


def test_readonly_connection_rejects_writes_and_never_creates(conn, db_file, tmp_path):
    store.save_papers([paper("R")], conn)
    ro = store.connect(str(db_file), readonly=True)
    try:
        assert ro.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 1
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("UPDATE papers SET title='x'")
        with pytest.raises(sqlite3.OperationalError):
            ro.execute("CREATE TABLE t(a)")
    finally:
        ro.close()
    assert conn.execute("SELECT title FROM papers").fetchone()["title"] == "R"
    with pytest.raises(FileNotFoundError):
        store.connect(str(tmp_path / "nope" / "x.db"), readonly=True)
    assert not (tmp_path / "nope").exists()
    with pytest.raises(ValueError):
        store.connect(str(db_file), readonly=True, create=True)
