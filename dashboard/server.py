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
import sys
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

        # API: 세션 체크
        if path == "/api/session":
            if self._is_authenticated():
                self._send_json({"ok": True})
            else:
                self._send_json({"error": "unauthorized"}, 401)
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

            # 인증 검증
            if (username == config.DASHBOARD_USERNAME and
                config.DASHBOARD_PASSWORD_HASH and
                bcrypt.checkpw(password.encode(), config.DASHBOARD_PASSWORD_HASH.encode())):
                _record_attempt(ip, True)
                token = _create_session(username)
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}")
                self.end_headers()
                self.wfile.write(json.dumps({"ok": True}).encode())
            else:
                _record_attempt(ip, False)
                self._send_json({"error": "invalid credentials"}, 401)
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
    parser.add_argument("--host", default="0.0.0.0", help="바인드 주소 (기본: 0.0.0.0)")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    server = http.server.HTTPServer((args.host, args.port), DashboardHandler)
    logger.info("Dashboard server: http://%s:%d", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("서버 종료")
        server.shutdown()


if __name__ == "__main__":
    main()
