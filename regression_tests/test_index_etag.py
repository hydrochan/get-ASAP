"""대시보드 진입 페이지(GET /) ETag·304 회귀 테스트 (dashboard/server.py 정적 분기).

  GET /                              -> 200, 강한 ETag(내용 해시), Cache-Control: no-cache
  GET / + If-None-Match: <그 ETag>    -> 304 (본문 없음), 같은 ETag
  내용이 바뀌면 ETag 도 바뀌고, 옛 ETag 로는 새 본문을 200 으로 받는다
  304 도 방문 기록(_record_visit)을 남긴다 / index.html 이 아닌 정적 파일에는 ETag 를 붙이지 않는다

합성 파일(tmp_path)만 서빙하며 실제 dashboard/ 와 DB 는 건드리지 않는다.
"""
import http.client
import http.server
import re
import threading

import pytest


@pytest.fixture
def web(tmp_path, monkeypatch):
    import server

    (tmp_path / "index.html").write_text("<p>v1</p>", encoding="utf-8")
    (tmp_path / "other.html").write_text("<p>other</p>", encoding="utf-8")
    monkeypatch.setattr(server, "DASHBOARD_DIR", str(tmp_path))
    visits = []
    monkeypatch.setattr(server, "_record_visit", visits.append)

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def get(path, **headers):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path, headers={"User-Agent": "Mozilla/5.0", **headers})
        resp = c.getresponse()
        body = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        c.close()
        return resp.status, hdrs, body

    get.index = tmp_path / "index.html"
    get.visits = visits
    yield get
    httpd.shutdown()
    httpd.server_close()


def test_index_has_strong_etag_and_no_cache(web):
    status, headers, body = web("/")
    assert status == 200
    assert body == b"<p>v1</p>"
    assert re.fullmatch(r'"[0-9a-f]{32}"', headers["etag"])   # 강한 ETag (W/ 없음)
    assert headers["cache-control"] == "no-cache"


def test_same_etag_for_same_content_and_for_index_html_path(web):
    _, h1, _ = web("/")
    _, h2, _ = web("/")
    _, h3, _ = web("/index.html")
    assert h1["etag"] == h2["etag"] == h3["etag"]


def test_if_none_match_gets_304_without_body(web):
    etag = web("/")[1]["etag"]
    status, headers, body = web("/", **{"If-None-Match": etag})
    assert status == 304
    assert body == b""
    assert headers["etag"] == etag
    assert headers["cache-control"] == "no-cache"


@pytest.mark.parametrize("fmt", ['W/{e}', '"nope", {e}', '*'])
def test_if_none_match_variants_get_304(web, fmt):
    etag = web("/")[1]["etag"]
    status, _, body = web("/", **{"If-None-Match": fmt.format(e=etag)})
    assert status == 304
    assert body == b""


def test_stale_or_wrong_if_none_match_gets_full_page(web):
    status, headers, body = web("/", **{"If-None-Match": '"0123456789abcdef0123456789abcdef"'})
    assert status == 200
    assert body == b"<p>v1</p>"
    assert "etag" in headers


def test_etag_changes_when_content_changes(web):
    old = web("/")[1]["etag"]
    web.index.write_text("<p>v2 — 바뀐 내용</p>", encoding="utf-8")
    status, headers, body = web("/", **{"If-None-Match": old})   # 옛 ETag 로 재검증해도 새 본문을 받는다
    assert status == 200
    assert "v2" in body.decode("utf-8")
    assert headers["etag"] != old
    assert web("/", **{"If-None-Match": headers["etag"]})[0] == 304


def test_304_still_counts_as_a_visit(web):
    etag = web("/")[1]["etag"]
    assert len(web.visits) == 1
    assert web("/", **{"If-None-Match": etag})[0] == 304
    assert len(web.visits) == 2


def test_other_static_files_get_no_etag(web):
    status, headers, _ = web("/other.html")
    assert status == 200
    assert "etag" not in headers
    assert "cache-control" not in headers
