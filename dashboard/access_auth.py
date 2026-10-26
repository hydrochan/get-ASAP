"""Cloudflare Access(Zero Trust) JWT 검증 - GET /manage 관리자 진입용.

신뢰 모델: 원본 VM 은 IP 로도 직접 닿을 수 있으므로 `Cf-Access-*` 헤더 값은 절대 그대로 믿지 않는다.
이 모듈은 `Cf-Access-Jwt-Assertion`(RS256 JWT)의 서명·aud·iss·exp 를 Cloudflare 공개키로 직접 검증하고,
검증된 `email` 클레임만으로 대시보드 사용자명을 결정한다. (`Cf-Access-Authenticated-User-Email` 은 읽지 않는다.)

설정(config.py, 모두 환경변수): ACCESS_TEAM_DOMAIN / ACCESS_AUD / ACCESS_EMAIL_USERS - 셋 다 있어야 켜진다.

공개키: `{ACCESS_TEAM_DOMAIN}/cdn-cgi/access/certs` 를 TLS 검증과 짧은 타임아웃으로 가져와 메모리에 캐시한다.
모르는 kid(키 교체)나 캐시 만료 때만 다시 가져오되, 위조 kid 폭주가 Cloudflare 호출로 번지지 않도록
갱신 시도 사이에 최소 간격(REFRESH_COOLDOWN)을 둔다. 갱신이 실패하면 기존 캐시를 유지한다.

이 모듈은 server.py 를 import 하지 않는다(server.py 가 `__main__` 으로 실행되면 모듈이 두 벌 생기기 때문).
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.request

import jwt

import config

logger = logging.getLogger(__name__)

CERTS_PATH = "/cdn-cgi/access/certs"
FETCH_TIMEOUT = 5          # 공개키 요청 타임아웃(초)
MAX_CERTS_BYTES = 1 << 20  # 공개키 응답 상한
KEYS_TTL = 3600            # 캐시 수명(초) - 지나면 다음 검증 때 갱신을 시도한다
REFRESH_COOLDOWN = 10      # 갱신 시도 최소 간격(초)
LEEWAY = 30                # 시계 오차 허용(초)
MAX_TOKEN_LEN = 8192
REQUIRED_CLAIMS = ["exp", "iat", "aud", "iss", "email"]

_lock = threading.Lock()
_keys: dict[str, jwt.PyJWK] = {}
_fetched_at = float("-inf")    # time.monotonic() 기준 마지막 성공 갱신
_last_attempt = float("-inf")  # time.monotonic() 기준 마지막 갱신 시도


class AccessAuthError(Exception):
    """검증 실패. reason 은 서버 로그용 짧은 코드이며 토큰이나 클레임 값은 담지 않는다.

    email 은 서명 검증을 통과한 뒤(매핑 실패 등)에만 채워진다.
    """

    def __init__(self, reason: str, email: str | None = None):
        super().__init__(reason)
        self.reason = reason
        self.email = email


def is_configured() -> bool:
    """세 설정이 모두 있고 팀 도메인이 https 일 때만 켜진다(urlopen 의 file:// 등 방지 겸용)."""
    return bool(
        config.ACCESS_TEAM_DOMAIN.startswith("https://")
        and config.ACCESS_AUD
        and config.ACCESS_EMAIL_USERS
    )


def reset_cache() -> None:
    """공개키 캐시를 비운다(테스트용)."""
    global _keys, _fetched_at, _last_attempt
    with _lock:
        _keys = {}
        _fetched_at = _last_attempt = float("-inf")


def _fetch_jwks() -> dict:
    """Cloudflare 공개키 JWKS 를 가져온다. urllib 기본 SSL 컨텍스트라 TLS 인증서·호스트명을 검증한다."""
    req = urllib.request.Request(
        config.ACCESS_TEAM_DOMAIN + CERTS_PATH,
        headers={"User-Agent": "get-ASAP-access-auth"},
    )
    with urllib.request.urlopen(req, timeout=FETCH_TIMEOUT) as resp:
        return json.loads(resp.read(MAX_CERTS_BYTES))


def _refresh_locked() -> None:
    global _keys, _fetched_at, _last_attempt
    _last_attempt = time.monotonic()
    try:
        jwk_set = jwt.PyJWKSet.from_dict(_fetch_jwks())
    except Exception as exc:  # 네트워크·파싱 오류 모두 - 기존 캐시를 유지한다
        logger.warning("Access 공개키 갱신 실패: %s", type(exc).__name__)
        return
    _keys = {k.key_id: k for k in jwk_set.keys if k.key_id}
    _fetched_at = time.monotonic()


def _get_key(kid: str):
    with _lock:
        key = _keys.get(kid)
        fresh = time.monotonic() - _fetched_at < KEYS_TTL
        if key is None or not fresh:
            if time.monotonic() - _last_attempt >= REFRESH_COOLDOWN:
                _refresh_locked()
            key = _keys.get(kid)
        return key


def authenticate(token: str) -> tuple[str, str]:
    """Access JWT 를 검증해 (정규화된 이메일, 대시보드 사용자명)을 돌려준다. 실패하면 AccessAuthError.

    관리자 여부(DASHBOARD_ADMINS)는 판정하지 않는다 - 호출 측(server.py)이 확인한다.
    """
    if not is_configured():
        raise AccessAuthError("not_configured")
    if not token or len(token) > MAX_TOKEN_LEN:
        raise AccessAuthError("malformed")
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError:
        raise AccessAuthError("malformed") from None
    kid = header.get("kid")
    if header.get("alg") != "RS256" or not isinstance(kid, str) or not kid:
        raise AccessAuthError("bad_header")
    key = _get_key(kid)
    if key is None:
        raise AccessAuthError("unknown_kid")

    try:
        claims = jwt.decode(
            token,
            key.key,
            algorithms=["RS256"],
            audience=config.ACCESS_AUD,
            issuer=config.ACCESS_TEAM_DOMAIN,
            leeway=LEEWAY,
            options={"require": REQUIRED_CLAIMS},
        )
    except jwt.ExpiredSignatureError:
        raise AccessAuthError("expired") from None
    except jwt.InvalidAudienceError:
        raise AccessAuthError("bad_audience") from None
    except jwt.InvalidIssuerError:
        raise AccessAuthError("bad_issuer") from None
    except jwt.InvalidSignatureError:
        raise AccessAuthError("bad_signature") from None
    except Exception as exc:  # 그 외 PyJWT 오류(누락 클레임·형식 오류 등) - 클래스명만 남긴다
        raise AccessAuthError(type(exc).__name__) from None

    # PyJWT 구버전의 iss 부분일치 결함(CVE-2024-53861)에 기대지 않도록 정확 일치를 한 번 더 확인한다.
    if claims.get("iss") != config.ACCESS_TEAM_DOMAIN:
        raise AccessAuthError("bad_issuer")
    email = claims.get("email")
    if not isinstance(email, str) or not email.strip():
        raise AccessAuthError("no_email")
    email = email.strip().lower()
    users = {str(k).strip().lower(): v for k, v in config.ACCESS_EMAIL_USERS.items()}
    username = users.get(email)
    if not username:
        raise AccessAuthError("unmapped_email", email)
    return email, username
