"""get-ASAP 대시보드 서버.

경량 HTTP 서버 + bcrypt 패스워드 인증 + 세션 관리.
Streamlit 대체용 — HTML+Tailwind 정적 대시보드를 서빙.

실행:
  python dashboard/server.py                # 기본 포트 8501
  python dashboard/server.py --port 8080    # 포트 지정
"""
import argparse
import hashlib
import http.server
import json
import logging
import os
import secrets
import sqlite3
import sys
import threading
import time
import urllib.parse

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import bcrypt
import config

logger = logging.getLogger(__name__)

# 경로 설정
DASHBOARD_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(os.path.dirname(DASHBOARD_DIR), "cache")
ACCESS_DB_PATH = os.path.join(os.path.dirname(DASHBOARD_DIR), "access_log.db")

# 접속 로그 DB (SQLite) — 스레드 안전을 위해 락과 함께 사용
_db_lock = threading.Lock()


def _init_access_db():
    """접속 로그 DB 초기화 (테이블 없으면 생성)"""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        # WAL 모드: reader-writer 동시성 개선. 한 번 설정하면 DB 파일에 영구 저장됨.
        # - read는 write를 블록하지 않음, write끼리만 직렬화
        # - synchronous=NORMAL: 약간의 내구성 완화로 write 처리량 향상 (크래시 시 최근 수초 로그 손실 가능, 통계 용도라 허용)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS access_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                event_type TEXT NOT NULL,
                username TEXT,
                ip TEXT,
                user_agent TEXT
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_ts ON access_events(ts)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_user ON access_events(username)")
        # 사용자 피드백 테이블
        conn.execute("""
            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts REAL NOT NULL,
                username TEXT,
                ip TEXT,
                category TEXT,
                message TEXT NOT NULL,
                user_agent TEXT,
                read_at REAL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_fb_ts ON feedback(ts)")
        conn.commit()
        conn.close()


def _log_event(event_type: str, username: str, ip: str, user_agent: str = ""):
    """접속 이벤트 기록 (실패해도 서버 동작에 영향 없음)"""
    try:
        with _db_lock:
            conn = sqlite3.connect(ACCESS_DB_PATH)
            conn.execute(
                "INSERT INTO access_events (ts, event_type, username, ip, user_agent) VALUES (?, ?, ?, ?, ?)",
                (time.time(), event_type, username, ip, user_agent[:200]),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning("이벤트 기록 실패: %s", e)


def _query_stats() -> dict:
    """접속 통계 집계"""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        conn.row_factory = sqlite3.Row
        now = time.time()
        day_ago = now - 86400
        week_ago = now - 7 * 86400
        month_ago = now - 30 * 86400

        def _count(where_sql: str, params=()) -> int:
            cur = conn.execute(f"SELECT COUNT(*) AS c FROM access_events WHERE {where_sql}", params)
            return cur.fetchone()["c"]

        def _unique_users(where_sql: str, params=()) -> int:
            cur = conn.execute(
                f"SELECT COUNT(DISTINCT username) AS c FROM access_events WHERE {where_sql} AND username != ''",
                params,
            )
            return cur.fetchone()["c"]

        out = {
            "total_logins": _count("event_type = 'login'"),
            "total_page_views": _count("event_type = 'page_view'"),
            "logins_24h": _count("event_type = 'login' AND ts >= ?", (day_ago,)),
            "logins_7d": _count("event_type = 'login' AND ts >= ?", (week_ago,)),
            "logins_30d": _count("event_type = 'login' AND ts >= ?", (month_ago,)),
            "unique_users_30d": _unique_users("ts >= ?", (month_ago,)),
            "unique_users_all": _unique_users("1 = 1"),
        }

        # 사용자별 집계
        cur = conn.execute("""
            SELECT username,
                   SUM(CASE WHEN event_type = 'login' THEN 1 ELSE 0 END) AS logins,
                   SUM(CASE WHEN event_type = 'page_view' THEN 1 ELSE 0 END) AS page_views,
                   MAX(ts) AS last_seen
            FROM access_events
            WHERE username != ''
            GROUP BY username
            ORDER BY logins DESC
        """)
        out["by_user"] = [dict(r) for r in cur.fetchall()]

        # 일별 로그인 (최근 30일)
        cur = conn.execute("""
            SELECT DATE(ts, 'unixepoch', 'localtime') AS day,
                   COUNT(*) AS c,
                   COUNT(DISTINCT username) AS unique_users
            FROM access_events
            WHERE event_type = 'login' AND ts >= ?
            GROUP BY day
            ORDER BY day DESC
        """, (month_ago,))
        out["daily_logins"] = [dict(r) for r in cur.fetchall()]

        # 최근 이벤트 (20건)
        cur = conn.execute("""
            SELECT ts, event_type, username, ip
            FROM access_events
            ORDER BY ts DESC
            LIMIT 20
        """)
        out["recent"] = [dict(r) for r in cur.fetchall()]

        conn.close()
        return out


# ---------- 피드백 CRUD ----------
MAX_FEEDBACK_LEN = 2000
ALLOWED_FEEDBACK_CATEGORIES = {"bug", "feature", "other"}


def _insert_feedback(username: str, ip: str, category: str, message: str, user_agent: str = "") -> int:
    """피드백 저장. id 반환."""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        cur = conn.execute(
            "INSERT INTO feedback (ts, username, ip, category, message, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), username, ip, category, message[:MAX_FEEDBACK_LEN], user_agent[:200]),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
    return fid


def _list_feedback(limit: int = 200) -> list:
    """피드백 목록 (최신순)."""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, ts, username, ip, category, message, read_at FROM feedback ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        conn.close()
        return rows


def _mark_feedback_read(fid: int, read: bool = True) -> bool:
    """피드백 읽음/안읽음 토글."""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        ts = time.time() if read else None
        cur = conn.execute("UPDATE feedback SET read_at = ? WHERE id = ?", (ts, fid))
        changed = cur.rowcount > 0
        conn.commit()
        conn.close()
    return changed


# 세션 저장소 (메모리)
sessions = {}  # token -> {"user": str, "expires": float}
SESSION_TTL = 3600  # 1시간

# 브루트포스 방지
login_attempts = {}  # ip -> {"count": int, "locked_until": float}
MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 300  # 5분


def _check_lockout(ip: str) -> bool:
    """잠금 상태 확인. True면 잠김."""
    info = login_attempts.get(ip)
    if not info:
        return False
    if info["locked_until"] and time.time() < info["locked_until"]:
        return True
    if info["locked_until"] and time.time() >= info["locked_until"]:
        # 잠금 해제
        del login_attempts[ip]
        return False
    return False


def _record_attempt(ip: str, success: bool):
    """로그인 시도 기록."""
    if success:
        login_attempts.pop(ip, None)
        return
    info = login_attempts.setdefault(ip, {"count": 0, "locked_until": None})
    info["count"] += 1
    if info["count"] >= MAX_ATTEMPTS:
        info["locked_until"] = time.time() + LOCKOUT_SECONDS
        logger.warning("브루트포스 잠금: %s (%d회 실패)", ip, info["count"])


def _create_session(user: str) -> str:
    """새 세션 토큰 생성."""
    token = secrets.token_hex(32)
    sessions[token] = {"user": user, "expires": time.time() + SESSION_TTL}
    return token


def _validate_session(token: str) -> bool:
    """세션 유효성 검증."""
    sess = sessions.get(token)
    if not sess:
        return False
    if time.time() > sess["expires"]:
        del sessions[token]
        return False
    return True


class DashboardHandler(http.server.BaseHTTPRequestHandler):
    """대시보드 HTTP 요청 핸들러."""

    def log_message(self, format, *args):
        logger.info(format, *args)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _get_session_token(self):
        cookie = self.headers.get("Cookie", "")
        for part in cookie.split(";"):
            part = part.strip()
            if part.startswith("session="):
                return part[8:]
        return None

    def _is_authenticated(self):
        token = self._get_session_token()
        return token and _validate_session(token)

    def _get_client_ip(self):
        return self.client_address[0]

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # API: 세션 체크 (페이지 조회 이벤트 기록)
        if path == "/api/session":
            token = self._get_session_token()
            sess = sessions.get(token) if token else None
            if sess and time.time() <= sess["expires"]:
                user = sess.get("user", "")
                _log_event("page_view", user, self._get_client_ip(), self.headers.get("User-Agent", ""))
                self._send_json({
                    "ok": True,
                    "user": user,
                    "is_admin": user in config.DASHBOARD_ADMINS,
                })
            else:
                self._send_json({"error": "unauthorized"}, 401)
            return

        # API: 피드백 목록 (관리자 전용)
        if path == "/api/feedback":
            token = self._get_session_token()
            sess = sessions.get(token) if token else None
            if not sess or time.time() > sess["expires"]:
                self._send_json({"error": "unauthorized"}, 401)
                return
            if sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                self._send_json({"items": _list_feedback()})
            except Exception as e:
                logger.exception("feedback list failed")
                self._send_json({"error": str(e)}, 500)
            return

        # API: 접속 통계 (관리자 전용)
        if path == "/api/stats":
            token = self._get_session_token()
            sess = sessions.get(token) if token else None
            if not sess or time.time() > sess["expires"]:
                self._send_json({"error": "unauthorized"}, 401)
                return
            if sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                self._send_json(_query_stats())
            except Exception as e:
                logger.exception("stats query failed")
                self._send_json({"error": str(e)}, 500)
            return

        # API: CSV 서빙
        if path.startswith("/api/csv/"):
            if not self._is_authenticated():
                self._send_json({"error": "unauthorized"}, 401)
                return
            month = path.split("/")[-1]
            # 경로 조작 방지
            if not all(c in "0123456789-" for c in month) or len(month) != 7:
                self._send_json({"error": "invalid month"}, 400)
                return
            csv_path = os.path.join(CACHE_DIR, f"papers_{month}.csv")
            if not os.path.exists(csv_path):
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.end_headers()
            with open(csv_path, "rb") as f:
                self.wfile.write(f.read())
            return

        # 정적 파일 서빙
        if path == "/" or path == "":
            path = "/index.html"

        # 보안: dashboard 디렉토리 내부만 서빙
        file_path = os.path.normpath(os.path.join(DASHBOARD_DIR, path.lstrip("/")))
        if not file_path.startswith(os.path.normpath(DASHBOARD_DIR)):
            self.send_response(403)
            self.end_headers()
            return

        if not os.path.isfile(file_path):
            self.send_response(404)
            self.end_headers()
            return

        # MIME 타입
        ext = os.path.splitext(file_path)[1].lower()
        mime = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
        }.get(ext, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", f"{mime}; charset=utf-8")
        self.end_headers()
        with open(file_path, "rb") as f:
            self.wfile.write(f.read())

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len) if content_len else b""

        # 로그인
        if path == "/api/login":
            ip = self._get_client_ip()
            if _check_lockout(ip):
                self._send_json({"error": "locked"}, 429)
                return

            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "bad request"}, 400)
                return

            username = data.get("username", "")
            password = data.get("password", "")

            # 다중 사용자 인증 (DASHBOARD_USERS dict + 레거시 단일 사용자 자동 병합)
            stored_hash = config.DASHBOARD_USERS.get(username, "")
            if (stored_hash and
                bcrypt.checkpw(password.encode(), stored_hash.encode())):
                _record_attempt(ip, True)
                token = _create_session(username)
                # 로그인 이벤트 기록
                _log_event("login", username, ip, self.headers.get("User-Agent", ""))
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                # X-Forwarded-Proto(Nginx가 전달) 기반으로 HTTPS 판단 → Secure 플래그 조건부 부여
                forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
                secure_flag = "; Secure" if forwarded_proto == "https" else ""
                self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}{secure_flag}")
                self.end_headers()
                self.wfile.write(json.dumps({
                    "ok": True,
                    "user": username,
                    "is_admin": username in config.DASHBOARD_ADMINS,
                }).encode())
            else:
                _record_attempt(ip, False)
                self._send_json({"error": "invalid credentials"}, 401)
            return

        # 피드백 제출 (로그인 사용자)
        if path == "/api/feedback":
            token = self._get_session_token()
            sess = sessions.get(token) if token else None
            if not sess or time.time() > sess["expires"]:
                self._send_json({"error": "unauthorized"}, 401)
                return
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "bad request"}, 400)
                return
            category = str(data.get("category", "other"))
            if category not in ALLOWED_FEEDBACK_CATEGORIES:
                category = "other"
            message = str(data.get("message", "")).strip()
            if not message:
                self._send_json({"error": "empty message"}, 400)
                return
            if len(message) > MAX_FEEDBACK_LEN:
                message = message[:MAX_FEEDBACK_LEN]
            try:
                fid = _insert_feedback(
                    username=sess.get("user", ""),
                    ip=self._get_client_ip(),
                    category=category,
                    message=message,
                    user_agent=self.headers.get("User-Agent", ""),
                )
                self._send_json({"ok": True, "id": fid})
            except Exception as e:
                logger.exception("feedback insert failed")
                self._send_json({"error": str(e)}, 500)
            return

        # 피드백 읽음 토글 (관리자 전용)
        if path == "/api/feedback/mark_read":
            token = self._get_session_token()
            sess = sessions.get(token) if token else None
            if not sess or time.time() > sess["expires"]:
                self._send_json({"error": "unauthorized"}, 401)
                return
            if sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "bad request"}, 400)
                return
            fid = data.get("id")
            read = bool(data.get("read", True))
            if not isinstance(fid, int):
                self._send_json({"error": "invalid id"}, 400)
                return
            ok = _mark_feedback_read(fid, read)
            self._send_json({"ok": ok})
            return

        # 로그아웃
        if path == "/api/logout":
            token = self._get_session_token()
            if token:
                sessions.pop(token, None)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Set-Cookie", "session=; Path=/; Max-Age=0")
            self.end_headers()
            self.wfile.write(json.dumps({"ok": True}).encode())
            return

        self._send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser(description="get-ASAP Dashboard Server")
    parser.add_argument("--port", type=int, default=8501, help="서버 포트 (기본: 8501)")
    parser.add_argument("--host", default="127.0.0.1", help="바인드 주소 (기본: 127.0.0.1 — Nginx 경유 전제. 직접 외부 노출 시 0.0.0.0)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    # 접속 로그 DB 초기화
    _init_access_db()
    logger.info("Access log DB: %s (admins=%s)", ACCESS_DB_PATH, sorted(config.DASHBOARD_ADMINS))

    # ThreadingHTTPServer: 요청마다 별도 스레드 처리 → 한 요청 블록/오류가 서버 전체를 hang시키지 않음
    server = http.server.ThreadingHTTPServer((args.host, args.port), DashboardHandler)
    server.daemon_threads = True  # 프로세스 종료 시 미완료 스레드 자동 정리
    server.request_queue_size = 128  # listen backlog 기본 5 → 128로 확대
    logger.info("Dashboard server (threading): http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("서버 종료")
        server.shutdown()


if __name__ == "__main__":
    main()
