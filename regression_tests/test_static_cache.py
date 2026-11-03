"""정적 파일 서빙 캐시 헤더 회귀 테스트 (dashboard/server.py 정적 분기).

  GET /static/x.css?v=<해시>  -> Cache-Control: public, max-age=31536000, immutable
  GET /static/x.css           -> Cache-Control 헤더 없음 (기존 동작)
  GET /static/x.html?v=<해시> -> html 은 v 가 있어도 immutable 아님

합성 파일(tmp_path)만 서빙하며 실제 dashboard/ 와 DB 는 건드리지 않는다.
"""
import http.client
import http.server
import threading

import pytest

IMMUTABLE = "public, max-age=31536000, immutable"


@pytest.fixture
def get(tmp_path, monkeypatch):
    import server

    static = tmp_path / "static"
    static.mkdir()
    (static / "x.css").write_text("body{color:red}", encoding="utf-8")
    (static / "x.js").write_text("var a=1;", encoding="utf-8")
    (static / "x.html").write_text("<p>hi</p>", encoding="utf-8")
    monkeypatch.setattr(server, "DASHBOARD_DIR", str(tmp_path))

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def _get(path):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", path)
        resp = c.getresponse()
        resp.read()
        headers = {k.lower(): v for k, v in resp.getheaders()}
        c.close()
        return resp.status, headers

    yield _get
    httpd.shutdown()
    httpd.server_close()


@pytest.mark.parametrize("name", ["x.css", "x.js"])
def test_versioned_css_js_is_immutable(get, name):
    status, headers = get(f"/static/{name}?v=abc123")
    assert status == 200
    assert headers["cache-control"] == IMMUTABLE


@pytest.mark.parametrize("path", ["/static/x.css", "/static/x.css?v=", "/static/x.html?v=abc123"])
def test_unversioned_or_non_asset_has_no_cache_header(get, path):
    status, headers = get(path)
    assert status == 200
    assert "cache-control" not in headers
