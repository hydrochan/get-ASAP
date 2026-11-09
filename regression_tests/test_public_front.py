"""공개 화면을 Pages 로 옮기는 서버 쪽 동작 회귀 테스트 (dashboard/server.py).

  GET / , /index.html 리다이렉트: PUBLIC_FRONT_ON_PAGES 켜짐 + 상태 파일에 front + 세션 없음 + stay=1 아님 -> 302 (쿼리 보존, stay 제거)
    꺼짐 / front 없음(옛 형식·CSV 만) / 유효 세션 / stay=1 / PAGES_BASE_URL 이 규칙에 안 맞음 -> 그대로 서빙(방문 기록 포함). /manage 는 영향 없음
  CORS: 공개 엔드포인트(/api/versions, /api/csv/*, /api/announcements, /api/metrics, /api/visit, 공개 POST /api/feedback)에만
    요청 Origin 이 허용 목록(PAGES_BASE_URL + PUBLIC_FRONT_ORIGINS)에 있을 때만 Access-Control-Allow-Origin: <그 Origin>(* 아님), 항상 Vary: Origin,
    자격증명 허용 없음. 관리자 엔드포인트에는 없다
  자체 도메인 전환: PUBLIC_FRONT_URL 이 리다이렉트 목적지, 그 호스트가 요청 Host 와 같으면 루프 방지로 그대로 서빙, 새 env 가 없으면 기존 동작
  POST /api/visit: 브라우저 UA 만 기록, 봇은 건너뜀, 204, raw IP 는 저장하지 않고 해시만
  크로스 오리진 피드백: text/plain 본문(= CORS 단순 요청, preflight 없음)을 JSON 으로 읽어 저장한다

합성 파일(tmp_path)과 가짜 기록 함수만 쓴다. 실제 dashboard/, DB, cache/ 는 건드리지 않는다.
"""
import http.client
import http.server
import json
import threading

import pytest

import store

PAGES = "https://demo-proj.pages.dev"
OWN = "https://front.example.com"
IP = "203.0.113.77"
BROWSER_UA = "Mozilla/5.0 (X11; Linux x86_64) Chrome/130"


@pytest.fixture
def web(tmp_path, monkeypatch):
    import server

    dash = tmp_path / "dash"
    dash.mkdir()
    (dash / "index.html").write_text("<p>origin front</p>", encoding="utf-8")
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(server, "DASHBOARD_DIR", str(dash))
    monkeypatch.setattr(server, "CACHE_DIR", str(cache))
    monkeypatch.setattr(store, "CACHE_DIR", str(cache))
    monkeypatch.setenv("PAGES_BASE_URL", PAGES)
    monkeypatch.setenv("PUBLIC_FRONT_ON_PAGES", "1")
    monkeypatch.delenv("PUBLIC_FRONT_ORIGINS", raising=False)
    monkeypatch.delenv("PUBLIC_FRONT_URL", raising=False)
    visits, feedback = [], []
    monkeypatch.setattr(server, "_record_visit", visits.append)
    monkeypatch.setattr(server, "_feedback_rate_limited", lambda ip: False)
    monkeypatch.setattr(server, "_insert_feedback", lambda **kw: feedback.append(kw) or 1)
    (cache / "announcements.json").write_text("[]", encoding="utf-8")

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def req(method, path, body=None, **headers):
        h = {"User-Agent": BROWSER_UA, "X-Real-IP": IP, **headers}
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request(method, path, body=body, headers=h)
        resp = c.getresponse()
        data = resp.read()
        hdrs = {k.lower(): v for k, v in resp.getheaders()}
        c.close()
        return resp.status, hdrs, data

    def publish_state(front="abc123def456"):
        state = {"base": PAGES, "months": {"2026-10": "v1"}}
        if front:
            state["front"] = front
        (cache / "pages_published.json").write_text(json.dumps(state), encoding="utf-8")

    req.server = server
    req.visits, req.feedback = visits, feedback
    req.publish_state = publish_state
    req.cache = cache
    yield req
    httpd.shutdown()
    httpd.server_close()


def session_cookie(web):
    return {"Cookie": f"session={web.server._create_session('someone')}"}


# ---------- 리다이렉트 ----------

def test_redirect_anonymous_to_pages_preserving_query_without_stay(web):
    web.publish_state()
    status, h, body = web("GET", "/?q=hydrogen&stay=0&x=1")
    assert status == 302
    assert h["location"] == f"{PAGES}/?q=hydrogen&x=1"
    assert h["cache-control"] == "no-store"
    assert body == b""
    assert web.visits == []  # 방문은 Pages 의 /api/visit 이 센다


@pytest.mark.parametrize("path", ["/", "/index.html"])
def test_redirect_plain_root_and_index_html(web, path):
    web.publish_state()
    status, h, _ = web("GET", path)
    assert status == 302 and h["location"] == f"{PAGES}/"


def test_flag_off_serves_origin(web, monkeypatch):
    web.publish_state()
    monkeypatch.setenv("PUBLIC_FRONT_ON_PAGES", "0")
    status, _, body = web("GET", "/")
    assert status == 200 and body == b"<p>origin front</p>"
    assert len(web.visits) == 1
    monkeypatch.delenv("PUBLIC_FRONT_ON_PAGES")
    assert web("GET", "/")[0] == 200


def test_front_not_published_serves_origin(web):
    assert web("GET", "/")[0] == 200  # 상태 파일 없음
    web.publish_state(front=None)  # 옛 형식(front 키 없음)
    assert web("GET", "/")[0] == 200


def test_valid_session_is_served_not_redirected(web):
    web.publish_state()
    status, _, body = web("GET", "/", **session_cookie(web))
    assert status == 200 and body == b"<p>origin front</p>"
    assert web("GET", "/", Cookie="session=forged")[0] == 302  # 무효 세션은 익명 취급


def test_stay_one_is_served(web):
    web.publish_state()
    status, _, body = web("GET", "/?stay=1")
    assert status == 200 and body == b"<p>origin front</p>"
    assert len(web.visits) == 1  # 서빙한 것은 기존대로 방문 기록


def test_bad_pages_base_url_disables_redirect(web, monkeypatch):
    web.publish_state()
    monkeypatch.setenv("PAGES_BASE_URL", "https://evil.example.com")
    assert web("GET", "/")[0] == 200


def test_redirect_goes_to_public_front_url(web, monkeypatch):
    web.publish_state()
    monkeypatch.setenv("PUBLIC_FRONT_URL", OWN)
    status, h, _ = web("GET", "/?q=h2&stay=0", Host="get-asap.hydrochan.com")
    assert status == 302 and h["location"] == f"{OWN}/?q=h2"


def test_redirect_self_loop_guard_serves_instead(web, monkeypatch):
    web.publish_state()
    monkeypatch.setenv("PUBLIC_FRONT_URL", OWN)
    status, _, body = web("GET", "/", Host="front.example.com")  # 이미 그 호스트로 들어왔다
    assert status == 200 and body == b"<p>origin front</p>"
    assert web("GET", "/", Host="front.example.com:443")[0] == 200  # 포트가 붙어도 같은 호스트
    assert web("GET", "/", Host="other.example.com")[0] == 302


def test_bad_public_front_url_falls_back_to_pages_base(web, monkeypatch):
    web.publish_state()
    for bad in ("http://front.example.com", "https://front.example.com/x", "front.example.com"):
        monkeypatch.setenv("PUBLIC_FRONT_URL", bad)
        status, h, _ = web("GET", "/")
        assert status == 302 and h["location"] == f"{PAGES}/"


def test_manage_and_other_paths_unaffected(web):
    web.publish_state()
    status, h, _ = web("GET", "/manage")
    assert "demo-proj.pages.dev" not in h.get("location", "")  # /manage 는 리다이렉트 대상이 아니다(Access 헤더 없으면 403 등)
    assert web("GET", "/api/session")[0] == 200


# ---------- CORS ----------

@pytest.mark.parametrize("method,path", [
    ("GET", "/api/versions"),
    ("GET", "/api/announcements"),
    ("GET", "/api/metrics"),
    ("GET", "/api/csv/2026-10?fmt=slim"),  # 파일이 없어 404 여도 헤더는 붙는다
    ("GET", "/api/csv/bad"),  # 400
    ("POST", "/api/visit"),
    ("POST", "/api/feedback"),
])
def test_cors_exact_origin_on_public_endpoints(web, method, path):
    status, h, _ = web(method, path, body=b'{"message":"hi"}' if method == "POST" and "feedback" in path else None, Origin=PAGES)
    assert h["access-control-allow-origin"] == PAGES  # '*' 가 아니라 정확한 출처
    assert h["vary"] == "Origin"
    assert "access-control-allow-credentials" not in h


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/stats"),
    ("GET", "/api/events"),
    ("GET", "/api/feedback"),  # 관리자 목록
    ("GET", "/api/session"),
    ("POST", "/api/login"),
    ("POST", "/api/feedback/mark_read"),
    ("POST", "/api/admin/review/papers"),
    ("GET", "/"),
])
def test_no_cors_on_admin_endpoints(web, method, path):
    _, h, _ = web(method, path, body=b"{}" if method == "POST" else None)
    assert "access-control-allow-origin" not in h


def test_cors_reflects_only_allowed_origins(web, monkeypatch):
    monkeypatch.setenv("PUBLIC_FRONT_ORIGINS", f" {OWN} , https://bad.example.com/x, http://plain.example.com ,")
    for origin in (PAGES, OWN):
        h = web("GET", "/api/versions", Origin=origin)[1]
        assert h["access-control-allow-origin"] == origin and h["vary"] == "Origin"
    for origin in ("https://evil.example.com", "https://bad.example.com", "http://plain.example.com", "null"):  # 형식 위반 항목은 허용 목록에 못 든다
        h = web("GET", "/api/versions", Origin=origin)[1]
        assert "access-control-allow-origin" not in h and h["vary"] == "Origin"
    h = web("GET", "/api/versions")[1]  # Origin 없는 요청
    assert "access-control-allow-origin" not in h and h["vary"] == "Origin"


def test_cors_own_domain_not_on_admin_endpoints(web, monkeypatch):
    monkeypatch.setenv("PUBLIC_FRONT_ORIGINS", OWN)
    for method, path in (("GET", "/api/stats"), ("GET", "/api/feedback"), ("GET", "/api/session"), ("POST", "/api/login")):
        h = web(method, path, body=b"{}" if method == "POST" else None, Origin=OWN)[1]
        assert "access-control-allow-origin" not in h


def test_cors_front_origins_work_without_pages_base(web, monkeypatch):
    monkeypatch.delenv("PAGES_BASE_URL")
    monkeypatch.setenv("PUBLIC_FRONT_ORIGINS", OWN)
    assert web("GET", "/api/versions", Origin=OWN)[1]["access-control-allow-origin"] == OWN
    assert "access-control-allow-origin" not in web("GET", "/api/versions", Origin=PAGES)[1]


def test_no_cors_when_pages_base_not_configured(web, monkeypatch):
    monkeypatch.delenv("PAGES_BASE_URL")
    assert "access-control-allow-origin" not in web("GET", "/api/versions", Origin=PAGES)[1]


# ---------- /api/visit ----------

def test_visit_records_browser_hash_only_and_returns_204(web):
    status, _, body = web("POST", "/api/visit")
    assert status == 204 and body == b""
    assert web.visits == [web.server._hash_ip(IP)]
    assert IP not in web.visits[0]  # raw IP 는 저장하지 않는다


@pytest.mark.parametrize("ua", ["Googlebot/2.1", "curl/8.0", "UptimeRobot monitor", "python-requests/2.31", "HeadlessChrome/120", ""])
def test_visit_skips_bots_and_missing_ua(web, ua):
    status, _, _ = web("POST", "/api/visit", **{"User-Agent": ua})
    assert status == 204
    assert web.visits == []


def test_visit_rejects_oversized_body(web):
    assert web("POST", "/api/visit", body=b"x" * 2000)[0] == 413
    assert web.visits == []


# ---------- 크로스 오리진 피드백 ----------

def test_cross_origin_feedback_text_plain_is_accepted(web):
    body = json.dumps({"category": "other", "message": "좋아요", "hp": ""}).encode("utf-8")
    status, h, data = web("POST", "/api/feedback", body=body,
                          **{"Content-Type": "text/plain;charset=UTF-8", "Origin": PAGES})
    assert status == 200 and json.loads(data)["ok"] is True
    assert h["access-control-allow-origin"] == PAGES
    assert len(web.feedback) == 1
    assert web.feedback[0]["message"] == "좋아요" and web.feedback[0]["username"] == ""  # 쿠키 없는 익명
