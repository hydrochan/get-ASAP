"""Cloudflare Access 로그인(GET /manage) 회귀 테스트 - docs/access-login.md. 합성 키·합성 토큰만 쓴다.

- 테스트 안에서 RSA 키쌍을 만들고 공개키 조회(_fetch_jwks)는 모킹한다. 네트워크 접근 없음.
- 실제 ThreadingHTTPServer 로 GET /manage 를 호출해 상태코드·Set-Cookie·세션 저장소를 확인한다.
- 검증 대상: 정상 토큰, aud/iss/exp/서명/필수 클레임/alg 오류, 모르는 kid 의 키 갱신(+쿨다운), 이메일 매핑,
  관리자 아님, Cf-Access-Authenticated-User-Email 위조, 기능 꺼짐·JWT 없음일 때 기존 Basic 경로 유지.
"""
import http.client
import http.server
import json
import logging
import threading
import time
import urllib.error
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import access_auth
import config

TEAM = "https://synthetic-team.cloudflareaccess.com"
AUD = "synthetic-aud-tag-0123456789"
EMAIL = "admin.user@example.invalid"
ADMIN = "pytest-admin"
PLAIN = "pytest-plain-user"
KID_A, KID_B = "kid-a", "kid-b"


def _keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwk(private_key, kid):
    doc = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key()))
    doc.update(kid=kid, alg="RS256", use="sig")
    return doc


@pytest.fixture(scope="module")
def keys():
    return SimpleNamespace(a=_keypair(), b=_keypair())


def make_token(key, kid=KID_A, alg="RS256", **claims):
    """Cloudflare Access 형태의 JWT. 값이 None 인 클레임은 뺀다."""
    now = int(time.time())
    payload = {"aud": [AUD], "email": EMAIL, "exp": now + 3600, "iat": now - 10, "iss": TEAM,
               "sub": "synthetic-sub", "type": "app"}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm=alg, headers={"kid": kid})


@pytest.fixture
def web(keys, tmp_path, monkeypatch):
    import server

    monkeypatch.setattr(config, "DASHBOARD_ADMINS", {ADMIN})
    monkeypatch.setattr(config, "ACCESS_TEAM_DOMAIN", TEAM)
    monkeypatch.setattr(config, "ACCESS_AUD", AUD)
    monkeypatch.setattr(config, "ACCESS_EMAIL_USERS", {EMAIL: ADMIN})
    monkeypatch.setattr(access_auth, "REFRESH_COOLDOWN", 0)
    access_auth.reset_cache()

    jwks = SimpleNamespace(doc={"keys": [_jwk(keys.a, KID_A)]}, calls=0, error=None)

    def fake_fetch():
        jwks.calls += 1
        if jwks.error:
            raise jwks.error
        return jwks.doc

    monkeypatch.setattr(access_auth, "_fetch_jwks", fake_fetch)
    # 감사 로그 DB 는 임시 경로로 격리(저장소의 access_log.db 를 만들지 않는다)
    monkeypatch.setattr(server, "ACCESS_DB_PATH", str(tmp_path / "access_log.db"))
    server._init_access_db()
    server.sessions.clear()

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def manage(headers=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/manage", headers=headers or {})
        resp = c.getresponse()
        resp.read()
        out = SimpleNamespace(status=resp.status, headers={k.lower(): v for k, v in resp.getheaders()})
        c.close()
        return out

    def jwt_header(token):
        return {"Cf-Access-Jwt-Assertion": token}

    yield SimpleNamespace(manage=manage, jwt_header=jwt_header, jwks=jwks, server=server)
    httpd.shutdown()
    httpd.server_close()
    server.sessions.clear()
    access_auth.reset_cache()


def assert_denied(web, resp):
    assert resp.status == 403
    assert "set-cookie" not in resp.headers and "location" not in resp.headers
    assert web.server.sessions == {}


def assert_logged_in(web, resp, user=ADMIN):
    assert resp.status == 302 and resp.headers["location"] == "/"
    cookie = resp.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    token = cookie.split(";")[0].removeprefix("session=")
    assert web.server.sessions[token]["user"] == user
    return token


# ---------- 정상 경로 ----------

def test_valid_token_creates_session_and_redirects(web, keys):
    resp = web.manage({**web.jwt_header(make_token(keys.a)), "X-Forwarded-Proto": "https"})
    assert_logged_in(web, resp)
    assert "; Secure" in resp.headers["set-cookie"]
    assert web.jwks.calls == 1


def test_valid_token_without_https_proto_has_no_secure_flag(web, keys):
    resp = web.manage(web.jwt_header(make_token(keys.a)))
    assert_logged_in(web, resp)
    assert "Secure" not in resp.headers["set-cookie"]


@pytest.mark.parametrize("token_email, mapping_key", [
    ("ADMIN.User@Example.INVALID", EMAIL),
    (EMAIL, "Admin.User@EXAMPLE.invalid"),
])
def test_email_compare_is_case_insensitive(web, keys, monkeypatch, token_email, mapping_key):
    monkeypatch.setattr(config, "ACCESS_EMAIL_USERS", {mapping_key: ADMIN})
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a, email=token_email))))


def test_expiry_leeway_is_small_but_nonzero(web, keys):
    now = int(time.time())
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a, exp=now - 5))))
    web.server.sessions.clear()
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a, exp=now - 120))))


def test_success_logs_email_but_never_the_token(web, keys, caplog):
    caplog.set_level(logging.INFO)
    token = make_token(keys.a)
    assert_logged_in(web, web.manage(web.jwt_header(token)))
    assert any(r.levelno == logging.INFO and EMAIL in r.getMessage() for r in caplog.records)
    assert token not in caplog.text


# ---------- 검증 실패는 전부 403(세션·쿠키 없음) ----------

@pytest.mark.parametrize("claims", [
    {"aud": ["some-other-aud"]},
    {"aud": "some-other-aud"},
    {"iss": "https://other-team.cloudflareaccess.com"},
    {"iss": TEAM + "/extra"},
    {"exp": int(time.time()) - 3600},
    {"iat": int(time.time()) + 3600},
])
def test_bad_claims_are_403(web, keys, claims):
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a, **claims))))


@pytest.mark.parametrize("claim", ["exp", "iat", "aud", "iss", "email"])
def test_missing_required_claim_is_403(web, keys, claim):
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a, **{claim: None}))))


def test_bad_signature_is_403(web, keys):
    # 알려진 kid 를 달았지만 다른 개인키로 서명
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.b, kid=KID_A))))


@pytest.mark.parametrize("token", ["", "not-a-jwt", "a.b.c", "x" * 20000])
def test_garbage_token_is_403(web, token):
    assert_denied(web, web.manage(web.jwt_header(token)))


def test_hs256_and_alg_none_are_rejected(web, keys):
    hs = make_token("synthetic-shared-secret-0123456789abcdef", alg="HS256")
    assert_denied(web, web.manage(web.jwt_header(hs)))
    unsigned = make_token(None, alg="none")
    assert_denied(web, web.manage(web.jwt_header(unsigned)))


def test_invalid_jwt_never_falls_back_to_basic(web, keys):
    # JWT 헤더가 있으면(깨진 값이어도) X-Basic-User 가 유효해도 Basic 으로 되돌아가지 않는다
    resp = web.manage({"Cf-Access-Jwt-Assertion": "garbage", "X-Basic-User": ADMIN})
    assert_denied(web, resp)


# ---------- 공개키 조회/갱신 ----------

def test_unknown_kid_triggers_refresh_then_succeeds(web, keys):
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a))))
    assert web.jwks.calls == 1
    web.server.sessions.clear()
    # Cloudflare 가 키를 교체: 새 kid 의 토큰이 오면 공개키 목록을 다시 가져온다
    web.jwks.doc = {"keys": [_jwk(keys.a, KID_A), _jwk(keys.b, KID_B)]}
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.b, kid=KID_B))))
    assert web.jwks.calls == 2


def test_unknown_kid_still_unknown_after_refresh_is_403(web, keys):
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.b, kid="never-published"))))
    assert web.jwks.calls == 1


def test_known_kid_uses_cache_without_refetching(web, keys):
    for _ in range(3):
        web.server.sessions.clear()
        assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a))))
    assert web.jwks.calls == 1


def test_refresh_cooldown_limits_forged_kid_flood(web, keys, monkeypatch):
    monkeypatch.setattr(access_auth, "REFRESH_COOLDOWN", 3600)
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a))))
    assert web.jwks.calls == 1
    web.server.sessions.clear()
    for i in range(5):
        assert_denied(web, web.manage(web.jwt_header(make_token(keys.b, kid=f"forged-{i}"))))
    assert web.jwks.calls == 1  # 위조 kid 가 몰려도 Cloudflare 호출은 늘지 않는다
    monkeypatch.setattr(access_auth, "REFRESH_COOLDOWN", 0)
    web.server.sessions.clear()
    web.jwks.doc = {"keys": [_jwk(keys.b, KID_B)]}
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.b, kid=KID_B))))
    assert web.jwks.calls == 2


def test_certs_fetch_failure_is_403_not_500_and_recovers(web, keys):
    web.jwks.error = urllib.error.URLError("synthetic network down")
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a))))
    web.jwks.error = None
    assert_logged_in(web, web.manage(web.jwt_header(make_token(keys.a))))


# ---------- 이메일 매핑 / 관리자 ----------

def test_email_not_mapped_is_403(web, keys, caplog):
    caplog.set_level(logging.INFO)
    token = make_token(keys.a, email="stranger@example.invalid")
    assert_denied(web, web.manage(web.jwt_header(token)))
    assert token not in caplog.text


def test_mapped_user_that_is_not_admin_is_403(web, keys, monkeypatch):
    monkeypatch.setattr(config, "ACCESS_EMAIL_USERS", {EMAIL: PLAIN})
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a))))


# ---------- 헤더 위조 ----------

def test_spoofed_email_header_without_jwt_is_not_login(web):
    assert_denied(web, web.manage({"Cf-Access-Authenticated-User-Email": EMAIL}))


def test_spoofed_email_header_does_not_override_jwt_email(web, keys):
    token = make_token(keys.a, email="stranger@example.invalid")
    resp = web.manage({**web.jwt_header(token), "Cf-Access-Authenticated-User-Email": EMAIL})
    assert_denied(web, resp)


# ---------- 기존 Basic 경로(전환 기간) ----------

def test_basic_fallback_still_works_when_access_configured_without_jwt(web):
    assert_logged_in(web, web.manage({"X-Basic-User": ADMIN}))
    assert web.jwks.calls == 0  # JWT 가 없으면 공개키 조회도 하지 않는다


@pytest.mark.parametrize("headers", [{}, {"X-Basic-User": ""}, {"X-Basic-User": PLAIN}])
def test_basic_fallback_denies_non_admin_when_access_configured(web, headers):
    assert_denied(web, web.manage(headers))


@pytest.mark.parametrize("unset", ["ACCESS_TEAM_DOMAIN", "ACCESS_AUD", "ACCESS_EMAIL_USERS"])
def test_feature_off_keeps_basic_path(web, keys, monkeypatch, unset):
    monkeypatch.setattr(config, unset, {} if unset == "ACCESS_EMAIL_USERS" else "")
    assert not access_auth.is_configured()
    assert_logged_in(web, web.manage({"X-Basic-User": ADMIN}))
    web.server.sessions.clear()
    # 꺼져 있으면 Cf-Access-Jwt-Assertion 이 와도 무시한다(유효한 토큰이어도 세션 없음, Basic 도 없으니 403)
    assert_denied(web, web.manage(web.jwt_header(make_token(keys.a))))
    # JWT 가 있어도 Basic 이 유효하면 예전 그대로 Basic 으로 처리
    assert_logged_in(web, web.manage({**web.jwt_header("garbage"), "X-Basic-User": ADMIN}))
    assert web.jwks.calls == 0


def test_feature_off_when_env_unset(monkeypatch):
    # 환경변수가 없을 때 config 기본값으로 꺼져 있어야 한다
    monkeypatch.setattr(config, "ACCESS_TEAM_DOMAIN", "")
    monkeypatch.setattr(config, "ACCESS_AUD", "")
    monkeypatch.setattr(config, "ACCESS_EMAIL_USERS", {})
    assert not access_auth.is_configured()
    with pytest.raises(access_auth.AccessAuthError):
        access_auth.authenticate("anything")


def test_http_team_domain_is_not_configured(monkeypatch):
    monkeypatch.setattr(config, "ACCESS_TEAM_DOMAIN", "http://synthetic-team.cloudflareaccess.com")
    monkeypatch.setattr(config, "ACCESS_AUD", AUD)
    monkeypatch.setattr(config, "ACCESS_EMAIL_USERS", {EMAIL: ADMIN})
    assert not access_auth.is_configured()
