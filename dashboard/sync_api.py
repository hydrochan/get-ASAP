"""동기화 API `/api/sync/v1/` - paper_autodown(KIST PC) <-> 정본 SQLite (Notion 탈출 설계 §5).

server.py가 `/api/sync/` prefix 요청을 `handle()`로 넘긴다. 이 모듈은 server.py를 import하지 않는다
(server.py가 `__main__`으로 실행되면 같은 모듈이 두 벌 생기기 때문). IP 잠금은 server.py가 콜백으로 넘긴다.

공통 규칙:
- 인증: `Authorization: Bearer <ASAP_SYNC_TOKEN>`. 토큰 미설정이면 모든 요청에 503. 비교는 hmac.compare_digest.
  실패는 401 JSON이며 IP 잠금(server.py의 로그인 잠금과 같은 로직, 카운터는 별도 키)은 인증에 실패한
  요청에만 적용한다(유효 토큰은 잠금과 무관하게 통과 - 엣지 IP 공유 대비).
  토큰은 URL·로그 어디에도 남기지 않는다.
- POST만 받는다(그 외 메서드는 405). 모든 응답에 `Cache-Control: no-store`.
- Content-Length가 2 MiB를 넘으면 본문을 읽기 전에 413.
- DB 파일이 없거나 열 수 없으면 503 `{"error":"db_unavailable"}`.
"""
from __future__ import annotations

import glob
import hmac
import json
import logging
import os
import re
import sqlite3

import store

logger = logging.getLogger(__name__)

PREFIX = "/api/sync/"
MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_LIMIT = 500
MAX_LIMIT = 1000
MAX_VERDICT_ITEMS = 200
_CHUNK = 64 * 1024
_BACKUP_NAME_RE = re.compile(r"^asap-\d{8}-\d{6}\.db\.gz$")


def _int_field(data: dict, key: str, default=None, *, minimum: int = 0, maximum: int | None = None):
    """JSON 정수 필드 검증(bool 거부). 누락이면 default, 잘못되면 ValueError."""
    if key not in data or data[key] is None:
        if default is None:
            raise ValueError(key)
        return default
    value = data[key]
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(key)
    return value


def latest_backup(db_path: str | None = None) -> str | None:
    """가장 최근 gzip 백업 파일 경로(이름의 타임스탬프 순). 없으면 None."""
    names = sorted(
        os.path.basename(p) for p in glob.glob(os.path.join(store.backup_dir(db_path), "asap-*.db.gz"))
    )
    names = [n for n in names if _BACKUP_NAME_RE.match(n)]
    return os.path.join(store.backup_dir(db_path), names[-1]) if names else None


class _Reply(Exception):
    """핸들러 중간에서 곧바로 응답을 내리기 위한 내부 신호."""

    def __init__(self, status: int, payload: dict):
        super().__init__(status)
        self.status = status
        self.payload = payload


def _send_json(handler, status: int, payload: dict, extra_headers: dict | None = None) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    for k, v in (extra_headers or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(body)


def _client_ip(handler) -> str:
    getter = getattr(handler, "_get_client_ip", None)
    return getter() if getter else handler.client_address[0]


def _bearer_token(handler) -> str:
    header = handler.headers.get("Authorization", "") or ""
    scheme, _, value = header.partition(" ")
    return value.strip() if scheme.lower() == "bearer" else ""


def _configured_token() -> str:
    """ASAP_SYNC_TOKEN. 미설정(공백 포함)이면 동기화 API 전체가 비활성(모든 요청 503)이다."""
    expected = os.getenv("ASAP_SYNC_TOKEN", "").strip()
    if not expected:
        raise _Reply(503, {"error": "sync_disabled"})
    return expected


def _authenticate(handler, expected: str, is_locked, record_attempt) -> None:
    """토큰 검증. 실패하면 _Reply를 던진다.

    토큰을 먼저 본다. 유효한 토큰은 IP 잠금과 무관하게 통과하고 카운터도 건드리지 않는다 - Cloudflare
    엣지 IP 를 여럿이 공유하므로, 남의 실패 때문에 정상 클라이언트가 잠기면 안 되고, 정상 요청이 남의 실패
    카운터를 지워 브루트포스를 풀어줘도 안 된다. 잠금 조회·증가는 인증에 실패한 요청에만 적용한다.
    """
    provided = _bearer_token(handler)
    if provided and hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return
    key = "sync:" + _client_ip(handler)  # 로그인 잠금과 같은 로직을 쓰되 카운터는 따로 둔다
    if is_locked and is_locked(key):
        raise _Reply(429, {"error": "locked"})
    if record_attempt:
        record_attempt(key, False)
    logger.warning("sync API 인증 실패")
    raise _Reply(401, {"error": "unauthorized"})


def _read_json_body(handler) -> dict:
    """Content-Length 상한을 읽기 전에 검사하고, JSON 객체 본문을 반환(빈 본문은 {})."""
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        raise _Reply(400, {"error": "bad_request"})
    if length < 0:
        raise _Reply(400, {"error": "bad_request"})
    if length > MAX_BODY_BYTES:
        raise _Reply(413, {"error": "payload_too_large"})
    handler._sync_body_read = True
    raw = handler.rfile.read(length) if length else b""
    if not raw.strip():
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError):
        raise _Reply(400, {"error": "bad_request"})
    if not isinstance(data, dict):
        raise _Reply(400, {"error": "bad_request"})
    return data


def _discard_unread_body(handler) -> None:
    """응답 전에, 아직 읽지 않은 요청 본문(상한 이내)을 버린다.

    본문을 안 읽고 연결을 닫으면 TCP RST 가 나가 클라이언트가 401/503 응답 대신 연결 오류를
    보고(Windows 실측: 토큰이 틀린 900 KB verdicts), 재시도 불가인 401 을 재시도하게 된다.
    상한(2 MiB)을 넘는 선언은 설계대로 읽지 않는다. 읽기 지연은 5초로 묶는다.
    """
    if getattr(handler, "_sync_body_read", False):
        return
    try:
        remaining = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        return
    if not 0 < remaining <= MAX_BODY_BYTES:
        return
    sock = handler.connection
    previous = sock.gettimeout()
    sock.settimeout(5)
    try:
        while remaining > 0:
            chunk = handler.rfile.read(min(_CHUNK, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
    except OSError:
        pass
    finally:
        sock.settimeout(previous)


def _open_db():
    try:
        return store.connect()
    except (FileNotFoundError, sqlite3.Error):
        raise _Reply(503, {"error": "db_unavailable"})


def _ping(conn, data: dict) -> dict:
    return {
        "ok": True,
        "last_seq": store.last_seq(conn),
        "schema_version": store.SCHEMA_VERSION,
        "db_epoch": store.db_epoch(conn),
    }


def _changes(conn, data: dict) -> dict:
    try:
        after = _int_field(data, "after", 0, minimum=0)
        limit = _int_field(data, "limit", DEFAULT_LIMIT, minimum=1, maximum=MAX_LIMIT)
    except ValueError:
        raise _Reply(400, {"error": "bad_request"})
    return store.fetch_changes(conn, after, limit)


def _verdicts(conn, data: dict) -> dict:
    items = data.get("items")
    if not isinstance(items, list) or len(items) > MAX_VERDICT_ITEMS:
        raise _Reply(400, {"error": "bad_request"})
    try:
        seen_through = _int_field(data, "seen_through", 0, minimum=0)
    except ValueError:
        raise _Reply(400, {"error": "bad_request"})
    return store.apply_verdicts(conn, items, seen_through)


_JSON_ENDPOINTS = {"ping": _ping, "changes": _changes, "verdicts": _verdicts}


def _send_backup(handler) -> None:
    path = latest_backup()
    if path is None:
        raise _Reply(404, {"error": "no_backup"})
    name = os.path.basename(path)
    try:
        size = os.path.getsize(path)
        src = open(path, "rb")
    except OSError:
        raise _Reply(404, {"error": "no_backup"})
    with src:
        handler.send_response(200)
        handler.send_header("Content-Type", "application/gzip")
        handler.send_header("Content-Length", str(size))
        handler.send_header("Cache-Control", "no-store")
        handler.send_header("X-Backup-Name", name)
        handler.end_headers()
        while True:
            chunk = src.read(_CHUNK)
            if not chunk:
                break
            handler.wfile.write(chunk)


def handle(handler, method: str, path: str, is_locked=None, record_attempt=None) -> None:
    """`/api/sync/...` 요청 1건을 처리해 응답까지 보낸다.

    Args:
        handler: BaseHTTPRequestHandler (headers/rfile/wfile 사용)
        method: "GET"/"POST" 등
        path: 쿼리스트링을 뗀 URL 경로
        is_locked: ip키 -> bool (server.py의 _check_lockout)
        record_attempt: (ip키, success) (server.py의 _record_attempt)
    """
    handler._sync_body_read = False
    try:
        expected = _configured_token()  # 미설정이면 메서드와 무관하게 모든 요청이 503
        if method != "POST":
            _send_json(handler, 405, {"error": "method_not_allowed"}, {"Allow": "POST"})
            return
        _authenticate(handler, expected, is_locked, record_attempt)
        endpoint = path[len(PREFIX):] if path.startswith(PREFIX) else ""
        if endpoint == "v1/backup/latest":
            _read_json_body(handler)
            conn = _open_db()
            conn.close()
            _send_backup(handler)
            return
        name = endpoint[len("v1/"):] if endpoint.startswith("v1/") else None
        func = _JSON_ENDPOINTS.get(name) if name else None
        if func is None:
            raise _Reply(404, {"error": "not_found"})
        data = _read_json_body(handler)
        conn = _open_db()
        try:
            payload = func(conn, data)
        except sqlite3.Error:
            logger.exception("sync API DB 오류: %s", name)
            raise _Reply(503, {"error": "db_unavailable"})
        finally:
            conn.close()
        _send_json(handler, 200, payload)
    except _Reply as r:
        _discard_unread_body(handler)
        _send_json(handler, r.status, r.payload)
    except (BrokenPipeError, ConnectionResetError):
        logger.info("sync API 클라이언트 연결 끊김")
    except Exception:
        logger.exception("sync API 처리 실패: %s", path)
        _send_json(handler, 500, {"error": "internal_error"})
