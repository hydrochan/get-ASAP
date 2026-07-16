"""get-ASAP 대시보드 서버.

경량 HTTP 서버 + bcrypt 패스워드 인증 + 세션 관리.
Streamlit 대체용 — HTML+Tailwind 정적 대시보드를 서빙.

실행:
  python dashboard/server.py                # 기본 포트 8501
  python dashboard/server.py --port 8080    # 포트 지정
"""
import argparse
import csv
import datetime
import hashlib
import http.server
import io
import json
import logging
import os
import re
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

# 방문자 지표에서 제외할 봇/크롤러/모니터링 User-Agent 패턴
_BOT_UA_RE = re.compile(
    r"bot|crawl|spider|slurp|bingpreview|facebookexternalhit|preview|"
    r"monitor|python-requests|curl|wget|headless|lighthouse",
    re.IGNORECASE,
)

# _hash_ip()가 만드는 sha256 hex digest(64자리) 형태 판별용 — 응답 직전 이중 해시 방지에 사용
_HEX64_RE = re.compile(r"^[0-9a-f]{64}$")

# 정적 서빙 화이트리스트: 실제 웹 자산 확장자만 허용. 그 외(*.py 등)는 404.
_ALLOWED_STATIC_EXT = {
    ".html", ".css", ".js", ".mjs", ".svg",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico",
    ".woff2", ".woff", ".ttf",
}

# ---------- 포커스 프로필 (맞춤 섹션 데이터) ----------
# 프론트엔드 소스에 하드코딩된 사용자 매핑을 피하기 위해 서버에서 관리.
# config.DASHBOARD_USER_PROFILES의 "focus_profile" 키가 여기 키를 참조.
# 데이터 자체는 민감 정보 아님(공개 화합물명). 민감한 건 "누가 보는가" = 프로필 매핑 (.env).
FOCUS_PROFILES = {
    "hydrogen_carriers": {
        "title": "Hydrogen Carrier Focus",
        "subtitle": "LOHC · Ammonia · SBH 관련 논문 하이라이트",
        "groups": [
            {
                "label": "LOHC",
                "keywordMatch": ["lohc"],
                "titleMatch": [
                    "methylcyclohexane",
                    "dibenzyltoluene",
                    "benzyltoluene",
                    "perhydro-dibenzyltoluene",
                    "perhydrodibenzyltoluene",
                    "n-ethylcarbazole",
                    "ethylcarbazole",
                    "dodecahydro-n-ethylcarbazole",
                    "decalin",
                    "dimethylbenzene",
                    "dimethyl benzene",
                    "dimethyl-benzene",
                    "liquid organic hydrogen carrier",
                    # LOHC 수소화 상태 약어 (H0/H6/H12/H18 등)
                    "h0-dbt", "h6-dbt", "h12-dbt", "h18-dbt",
                    "h0dbt", "h6dbt", "h12dbt", "h18dbt",
                    "h12-nec", "h12nec",
                    "h6-mch", "h6mch",
                    "h10-naphthalene", "h10naphthalene",
                    "h12-bt", "h12bt",  # benzyltoluene 수소화
                ],
            },
            {
                "label": "Ammonia",
                "keywordMatch": ["ammonia"],
                "titleMatch": ["ammonia", "nh3", "nh_3"],
            },
            {
                "label": "SBH",
                "keywordMatch": ["sbh", "borohydride"],
                "titleMatch": [
                    "sodium borohydride",
                    "sodium-borohydride",
                    "nabh4",
                    "nabh_4",
                    "na bh4",
                    "borohydride",
                ],
            },
        ],
    },
}


def _build_user_payload(username: str) -> dict:
    """로그인/세션 응답에 담을 사용자 맞춤 필드 계산.

    hidden_sections, focus_config를 서버에서 결정해 내려주므로
    프론트엔드는 username → 프로필 매핑을 몰라도 됨.
    """
    profile = config.DASHBOARD_USER_PROFILES.get(username, {})
    hidden_sections = list(profile.get("hidden_sections", []))
    focus_key = profile.get("focus_profile")
    focus_config = FOCUS_PROFILES.get(focus_key) if focus_key else None
    return {
        "hidden_sections": hidden_sections,
        "focus_config": focus_config,
    }

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
        # 방문자 지표 테이블 (익명 방문자 포함).
        # PRIVACY: raw IP는 저장하지 않는다 — salted hash(visit_hash)만 저장.
        # upsert 구조: visit_hash당 1행. 각 행이 곧 distinct visitor이고
        # last_seen이 그 방문자의 최근 방문 시각 → online/today/total 모두 정확히 distinct 집계 가능.
        conn.execute("""
            CREATE TABLE IF NOT EXISTS visits (
                visit_hash TEXT PRIMARY KEY,
                first_seen REAL NOT NULL,
                last_seen REAL NOT NULL
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_visits_last_seen ON visits(last_seen)")
        conn.commit()
        conn.close()


def _hash_ip(ip: str) -> str:
    """방문자 지표(visit_hash)와 동일한 salt·알고리즘으로 IP를 해시.

    PRIVACY: 로그인/조회 이벤트 등 access_events에 IP를 남길 때는 항상 이 헬퍼를 거쳐
    raw IP 대신 해시만 저장한다(_maybe_record_visit의 visit_hash 계산과 동일 방식으로 일원화).
    """
    return hashlib.sha256((config.VISIT_HASH_SALT + ip).encode("utf-8")).hexdigest()


def _ip_hash_for_display(stored_value: str) -> str:
    """DB에 저장된 access_events.ip 값을 API 응답용 해시로 변환.

    앞으로 저장되는 값은 이미 _hash_ip를 거친 64자리 hex이므로 그대로 반환(이중 해시 방지).
    과거(정책 변경 이전)에 저장된 원시 IP는 마이그레이션하지 않으므로, 응답 직전에 여기서
    해시해 raw IP가 API 응답에 노출되지 않도록 보장한다.
    """
    if not stored_value:
        return _hash_ip("")
    if _HEX64_RE.match(stored_value):
        return stored_value
    return _hash_ip(stored_value)


def _log_event(event_type: str, username: str, ip: str, user_agent: str = ""):
    """접속 이벤트 기록 (실패해도 서버 동작에 영향 없음).

    PRIVACY: raw IP는 저장하지 않는다 — _hash_ip로 해시한 값만 access_events.ip에 기록.
    """
    try:
        with _db_lock:
            conn = sqlite3.connect(ACCESS_DB_PATH)
            conn.execute(
                "INSERT INTO access_events (ts, event_type, username, ip, user_agent) VALUES (?, ?, ?, ?, ?)",
                (time.time(), event_type, username, _hash_ip(ip), user_agent[:200]),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning("이벤트 기록 실패: %s", e)


def _query_stats() -> dict:
    """접속 통계 집계 (익명 방문자 기준)"""
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        conn.row_factory = sqlite3.Row
        now = time.time()
        month_ago = now - 30 * 86400

        def _count(where_sql: str, params=()) -> int:
            cur = conn.execute(f"SELECT COUNT(*) AS c FROM access_events WHERE {where_sql}", params)
            return cur.fetchone()["c"]

        out = {
            "total_page_views": _count("event_type = 'page_view'"),
            "unique_visitors_30d": conn.execute(
                "SELECT COUNT(*) AS c FROM visits WHERE last_seen >= ?", (month_ago,)
            ).fetchone()["c"],
            "unique_visitors_all": conn.execute(
                "SELECT COUNT(*) AS c FROM visits"
            ).fetchone()["c"],
        }

        # 일별 페이지뷰 (최근 30일)
        cur = conn.execute("""
            SELECT DATE(ts, 'unixepoch', 'localtime') AS day,
                   COUNT(*) AS c
            FROM access_events
            WHERE event_type = 'page_view' AND ts >= ?
            GROUP BY day
            ORDER BY day ASC
        """, (month_ago,))
        out["daily_page_views"] = [dict(r) for r in cur.fetchall()]

        # 최근 이벤트 (20건). PRIVACY: ip 컬럼은 절대 그대로 노출하지 않고 ip_hash로 통일.
        cur = conn.execute("""
            SELECT ts, event_type, username, ip
            FROM access_events
            ORDER BY ts DESC
            LIMIT 20
        """)
        out["recent"] = [
            {
                "ts": r["ts"],
                "event_type": r["event_type"],
                "username": r["username"],
                "ip_hash": _ip_hash_for_display(r["ip"] or ""),
            }
            for r in cur.fetchall()
        ]

        conn.close()
        return out


def _query_events(offset: int, limit: int, date_from: str | None, date_to: str | None,
                   event_type: str, q: str) -> dict:
    """Recent Events 페이지네이션/필터/검색 (access_events + feedback 통합, 최신순).

    login/page_view는 access_events, feedback은 feedback 테이블에서 오므로 UNION ALL로 합친 뒤
    필터·정렬·LIMIT/OFFSET을 모두 SQL 레벨에서 처리한다. 값은 전부 바인딩 파라미터로 전달(SQLi 방지).
    """
    union_sql = (
        "SELECT ts, event_type AS type, username AS user, ip FROM access_events "
        "UNION ALL "
        "SELECT ts, 'feedback' AS type, username AS user, ip FROM feedback"
    )
    where_parts = []
    params: list = []

    if event_type != "all":
        where_parts.append("type = ?")
        params.append(event_type)
    if date_from:
        try:
            ts_from = datetime.datetime.strptime(date_from, "%Y-%m-%d").timestamp()
            where_parts.append("ts >= ?")
            params.append(ts_from)
        except ValueError:
            pass  # 잘못된 날짜 형식은 관대하게 무시
    if date_to:
        try:
            # to는 해당일 포함이므로 다음날 자정 미만으로 upper bound 설정
            ts_to = (datetime.datetime.strptime(date_to, "%Y-%m-%d") + datetime.timedelta(days=1)).timestamp()
            where_parts.append("ts < ?")
            params.append(ts_to)
        except ValueError:
            pass
    if q:
        # LIKE 와일드카드(%, _)와 이스케이프 문자(\) 자체를 이스케이프해 리터럴 검색으로 처리
        # (백슬래시부터 먼저 이스케이프해야 이후 %, _ 치환으로 생긴 백슬래시가 이중 이스케이프되지 않음)
        q_escaped = q.lower().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_parts.append("LOWER(user) LIKE ? ESCAPE '\\'")
        params.append(f"%{q_escaped}%")

    where_sql = f"WHERE {' AND '.join(where_parts)}" if where_parts else ""

    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        conn.row_factory = sqlite3.Row
        total = conn.execute(
            f"SELECT COUNT(*) AS c FROM ({union_sql}) {where_sql}", params
        ).fetchone()["c"]
        cur = conn.execute(
            f"SELECT * FROM ({union_sql}) {where_sql} ORDER BY ts DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
        )
        rows = cur.fetchall()
        conn.close()

    events = [
        {
            "ts": r["ts"],
            "type": r["type"],
            "user": r["user"] or "",
            "ip_hash": _ip_hash_for_display(r["ip"] or ""),
        }
        for r in rows
    ]
    return {"events": events, "total": int(total)}


def _record_visit(visit_hash: str):
    """방문 기록 upsert (실패해도 서버 동작에 영향 없음).

    PRIVACY: 인자는 이미 salt로 해시된 visit_hash이며, raw IP는 절대 저장하지 않는다.
    visit_hash가 처음이면 새 행(first_seen=last_seen=now), 이미 있으면 last_seen만 갱신.
    """
    try:
        now = time.time()
        with _db_lock:
            conn = sqlite3.connect(ACCESS_DB_PATH)
            conn.execute(
                "INSERT INTO visits (visit_hash, first_seen, last_seen) VALUES (?, ?, ?) "
                "ON CONFLICT(visit_hash) DO UPDATE SET last_seen = excluded.last_seen",
                (visit_hash, now, now),
            )
            conn.commit()
            conn.close()
    except Exception as e:
        logger.warning("방문 기록 실패: %s", e)


def _query_metrics() -> dict:
    """방문자 지표 집계 — 모두 distinct visitor(visit_hash) 기준.

    online = 최근 5분 내 활동한 distinct 방문자
    today  = 서버 로컬 자정 이후 활동한 distinct 방문자
    total  = 전체 기간 distinct 방문자

    upsert 테이블이라 각 행이 곧 distinct visitor이고 last_seen이 최근 방문 시각이므로,
    "구간 내 최소 1회 방문" == "last_seen이 구간 하한 이상"이 되어 세 값 모두 정확하다.
    (어제와 오늘 모두 방문한 사람은 last_seen이 오늘 → today에 정확히 포함됨.)
    """
    now = time.time()
    online_cutoff = now - 300  # 5분
    midnight = datetime.datetime.now().replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        try:
            online = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE last_seen >= ?", (online_cutoff,)
            ).fetchone()[0]
            today = conn.execute(
                "SELECT COUNT(*) FROM visits WHERE last_seen >= ?", (midnight,)
            ).fetchone()[0]
            total = conn.execute("SELECT COUNT(*) FROM visits").fetchone()[0]
        finally:
            conn.close()
    return {"online": int(online), "today": int(today), "total": int(total)}


# ---------- 피드백 CRUD ----------
MAX_FEEDBACK_LEN = 2000
ALLOWED_FEEDBACK_CATEGORIES = {"bug", "feature", "other"}
MAX_FEEDBACK_BODY_LEN = 16384  # 본문 크기 상한(바이트) — 이걸 넘으면 read 전에 413으로 차단
# 익명 게스트 허용에 따른 IP(해시) 기준 도배 방지 — NAT 공유 기관망은 통과, 단일 플러더는 차단
FEEDBACK_MIN_INTERVAL_SEC = 15  # 같은 IP 연속 제출 최소 간격(초)
FEEDBACK_MAX_PER_HOUR = 20  # 같은 IP 시간당 최대 제출 수


def _insert_feedback(username: str, ip: str, category: str, message: str, user_agent: str = "") -> int:
    """피드백 저장. id 반환.

    PRIVACY: 다른 access_events와 동일하게 raw IP는 저장하지 않고 _hash_ip로 해시해 기록한다.
    """
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        cur = conn.execute(
            "INSERT INTO feedback (ts, username, ip, category, message, user_agent) VALUES (?, ?, ?, ?, ?, ?)",
            (time.time(), username, _hash_ip(ip), category, message[:MAX_FEEDBACK_LEN], user_agent[:200]),
        )
        fid = cur.lastrowid
        conn.commit()
        conn.close()
    return fid


def _feedback_rate_limited(ip: str) -> bool:
    """IP(해시) 기준 피드백 제출 빈도 제한.

    익명 게스트에게 제출을 개방하면서 도배를 막기 위한 안전장치.
    feedback.ip 컬럼은 이미 _hash_ip를 거친 값으로 저장되므로 조회 시에도
    동일하게 해시해 비교한다(원본 IP는 저장/보관하지 않음).
    """
    ip_hash = _hash_ip(ip)
    now = time.time()
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        try:
            recent = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE ip = ? AND ts > ?",
                (ip_hash, now - FEEDBACK_MIN_INTERVAL_SEC),
            ).fetchone()[0]
            if recent > 0:
                return True
            hourly = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE ip = ? AND ts > ?",
                (ip_hash, now - 3600),
            ).fetchone()[0]
            if hourly >= FEEDBACK_MAX_PER_HOUR:
                return True
        finally:
            conn.close()
    return False


def _list_feedback(limit: int = 200) -> list:
    """피드백 목록 (최신순).

    PRIVACY: raw ip 컬럼은 응답에 그대로 내보내지 않고 ip_hash로 통일(_ip_hash_for_display가
    레거시 raw IP 행과 신규 해시 저장 행 모두 올바르게 해시로 변환).
    """
    with _db_lock:
        conn = sqlite3.connect(ACCESS_DB_PATH)
        conn.row_factory = sqlite3.Row
        cur = conn.execute(
            "SELECT id, ts, username, ip, category, message, read_at FROM feedback ORDER BY ts DESC LIMIT ?",
            (limit,),
        )
        rows = []
        for r in cur.fetchall():
            row = dict(r)
            row["ip_hash"] = _ip_hash_for_display(row.pop("ip") or "")
            rows.append(row)
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

# 브루트포스 방지 (NAT 공유 환경 대응 — 연구실 공용 와이파이처럼 여러 명이 같은 공인 IP 뒤에 있는 경우
# 너무 빡빡하면 한 사람 오타가 전체 네트워크 차단으로 이어짐. bcrypt + 세션으로 실제 공격 난이도는 충분)
login_attempts = {}  # ip -> {"count": int, "locked_until": float}
MAX_ATTEMPTS = 20
LOCKOUT_SECONDS = 120  # 2분


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
    """세션 유효성 검증 + 슬라이딩 갱신.

    검증에 성공하면 세션 expires를 now + SESSION_TTL로 연장한다(활동 중인 세션이
    고정 TTL로 끊기지 않도록). 슬라이딩 갱신은 이 함수 한 곳에서만 수행한다.
    """
    sess = sessions.get(token)
    if not sess:
        return False
    if time.time() > sess["expires"]:
        # pop(token, None): ThreadingHTTPServer 환경에서 동시 요청이 같은 토큰을 만료 처리할 때
        # 이미 다른 스레드가 지운 키를 다시 지우려다 KeyError 나는 레이스를 방지
        sessions.pop(token, None)
        return False
    sess["expires"] = time.time() + SESSION_TTL
    return True


def _strip_csv_column(raw_text: str, column: str) -> str:
    """CSV 텍스트에서 지정한 이름의 컬럼을 제거해 반환.

    csv 모듈로 파싱/재직렬화하므로 따옴표로 감싼 필드 내부의 쉼표·줄바꿈이
    보존되고 헤더/행 정렬이 깨지지 않는다. 컬럼명이 헤더에 없으면 원본을 그대로 반환.
    저장 포맷과 동일하게 QUOTE_ALL로 재직렬화한다.
    """
    reader = csv.reader(io.StringIO(raw_text))
    rows = list(reader)
    if not rows:
        return raw_text
    header = rows[0]
    try:
        idx = header.index(column)
    except ValueError:
        # 제거할 컬럼이 없음 → 원본 그대로 (예: 이미 status 없는 캐시)
        return raw_text
    out = io.StringIO()
    writer = csv.writer(out, quoting=csv.QUOTE_ALL, lineterminator="\n")
    for row in rows:
        if idx < len(row):
            del row[idx]
        writer.writerow(row)
    return out.getvalue()


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
        """실제 클라이언트 IP 획득.

        Nginx reverse proxy 뒤에 있으므로 self.client_address[0]은 항상 127.0.0.1.
        Nginx가 설정해주는 X-Real-IP / X-Forwarded-For 헤더에서 원본 IP를 추출.
        127.0.0.1에 bind되어 있어 외부에서 헤더 위조가 불가능하므로 안전하게 신뢰.
        """
        # X-Real-IP 우선 (Nginx에서 단일 값으로 넘어옴)
        real_ip = self.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
        # X-Forwarded-For: "client, proxy1, proxy2" → 가장 왼쪽이 원본 client
        xff = self.headers.get("X-Forwarded-For", "").strip()
        if xff:
            return xff.split(",")[0].strip()
        return self.client_address[0]

    def _maybe_record_visit(self):
        """대시보드 진입(index.html 서빙) 시 방문자 지표 기록.

        봇/크롤러/모니터링/CLI는 User-Agent로 제외한다.
        PRIVACY: raw IP는 저장하지 않는다 — salt로 해시한 visit_hash만 visits 테이블에 기록.
        raw IP는 해시 계산에만 잠시 쓰이고 어디에도 저장/로깅되지 않는다.
        """
        ua = self.headers.get("User-Agent", "")
        if not ua or _BOT_UA_RE.search(ua):
            return
        ip = self._get_client_ip()
        _record_visit(_hash_ip(ip))

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # API: 세션 체크 (페이지 조회 이벤트 기록)
        if path == "/api/session":
            token = self._get_session_token()
            # _validate_session 성공 시 세션 슬라이딩 갱신도 함께 수행됨(요구사항 4)
            if token and _validate_session(token):
                sess = sessions.get(token)
                user = sess.get("user", "")
                _log_event("page_view", user, self._get_client_ip(), self.headers.get("User-Agent", ""))
                payload = {
                    "ok": True,
                    "user": user,
                    "is_admin": user in config.DASHBOARD_ADMINS,
                }
                payload.update(_build_user_payload(user))
                self._send_json(payload)
            else:
                # 게스트(비로그인): 401 대신 빈 게스트 페이로드를 200으로 반환 (contract #2).
                # _log_event를 호출하지 않으므로 익명 방문자 raw IP는 기록되지 않는다.
                guest = {"ok": True, "user": "", "is_admin": False}
                guest.update(_build_user_payload(""))
                self._send_json(guest)
            return

        # API: 방문자 지표 (PUBLIC — 비로그인 접근 허용, contract #1)
        # 200 JSON: {"online": <int>, "today": <int>, "total": <int>}
        if path == "/api/metrics":
            try:
                self._send_json(_query_metrics())
            except Exception:
                logger.exception("metrics query failed")
                # 계약 유지: 실패해도 세 키를 가진 200 JSON을 반환
                self._send_json({"online": 0, "today": 0, "total": 0})
            return

        # API: 피드백 목록 (관리자 전용)
        if path == "/api/feedback":
            token = self._get_session_token()
            # admin 보호 패턴: 세션 무효면 401, 유효하나 admin 아니면 403 (_validate_session이 슬라이딩 갱신도 수행)
            if not token or not _validate_session(token):
                self._send_json({"error": "unauthorized"}, 401)
                return
            sess = sessions.get(token)
            if not sess or sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                self._send_json({"items": _list_feedback()})
            except Exception as e:
                logger.exception("feedback list failed")
                self._send_json({"error": "internal server error"}, 500)
            return

        # API: 접속 통계 (관리자 전용)
        if path == "/api/stats":
            token = self._get_session_token()
            # admin 보호 패턴: 세션 무효면 401, 유효하나 admin 아니면 403 (_validate_session이 슬라이딩 갱신도 수행)
            if not token or not _validate_session(token):
                self._send_json({"error": "unauthorized"}, 401)
                return
            sess = sessions.get(token)
            if not sess or sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                self._send_json(_query_stats())
            except Exception as e:
                logger.exception("stats query failed")
                self._send_json({"error": "internal server error"}, 500)
            return

        # API: Recent Events 페이지네이션/필터/검색 (관리자 전용)
        # 쿼리: offset(기본0) limit(기본25,상한100) from/to(YYYY-MM-DD) type(all|login|page_view|feedback) q(user 부분일치)
        # 200 JSON: {"events": [{"ts","type","user","ip_hash"}, ...], "total": <필터 적용 전체 건수>}
        if path == "/api/events":
            token = self._get_session_token()
            # admin 보호 패턴: 세션 무효면 401, 유효하나 admin 아니면 403 (_validate_session이 슬라이딩 갱신도 수행)
            if not token or not _validate_session(token):
                self._send_json({"error": "unauthorized"}, 401)
                return
            sess = sessions.get(token)
            if not sess or sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return

            qs = urllib.parse.parse_qs(parsed.query)

            def _qparam(key, default=""):
                vals = qs.get(key)
                return vals[0] if vals else default

            try:
                offset = int(_qparam("offset", "0"))
            except ValueError:
                offset = 0
            offset = max(0, offset)
            try:
                limit = int(_qparam("limit", "25"))
            except ValueError:
                limit = 25
            limit = max(1, min(limit, 100))  # 상한 100은 강제
            date_from = _qparam("from", "").strip() or None
            date_to = _qparam("to", "").strip() or None
            event_type = _qparam("type", "all").strip().lower()
            if event_type not in ("all", "login", "page_view", "feedback"):
                event_type = "all"  # 잘못된 값은 관대하게 all로 폴백
            q = _qparam("q", "").strip()[:100]  # 길이 상한 100자 (과도한 LIKE 패턴 방지)

            try:
                self._send_json(_query_events(offset, limit, date_from, date_to, event_type, q))
            except Exception:
                logger.exception("events query failed")
                self._send_json({"error": "internal server error"}, 500)
            return

        # API: 공지·개선사항 카드용 JSON — 이제 PUBLIC (비로그인 접근 허용, contract #4).
        if path == "/api/announcements":
            # cache/announcements.json 이 없으면 빈 배열 반환.
            ann_path = os.path.join(CACHE_DIR, "announcements.json")
            if not os.path.exists(ann_path):
                self._send_json([])
                return
            try:
                with open(ann_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._send_json(data)
            except Exception as e:
                self._send_json({"error": f"announcements parse fail: {e}"}, 500)
            return

        # API: 월별 논문 CSV — 이제 PUBLIC (비로그인 접근 허용, contract #3).
        # status 컬럼은 서빙 시 제거해서 내려준다(캐시 파일 자체는 status를 유지하되 유출 안 됨).
        if path.startswith("/api/csv/"):
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
            try:
                # 캐시는 utf-8-sig(BOM)+QUOTE_ALL로 저장됨. BOM을 벗겨 파싱 후 status 제거.
                with open(csv_path, "r", encoding="utf-8-sig") as f:
                    raw = f.read()
                body = _strip_csv_column(raw, "status").encode("utf-8-sig")
            except Exception:
                logger.exception("csv serve failed: %s", month)
                self.send_response(500)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/csv; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        # ---------- 관리자 진입: GET /manage ----------
        # 신뢰 모델(TRUST MODEL):
        #   - 이 앱은 127.0.0.1 에만 bind 된다(main()의 기본 --host). 외부에서 도달하는 유일한 ingress 는 Nginx 다.
        #   - Nginx 는 오직 /manage location 에만 Basic 인증을 걸고, 인증에 성공한 사용자명을
        #     X-Basic-User=$remote_user 헤더로 담아 '이 경로로만' 프록시한다.
        #   - Nginx 는 그 외 모든 location 에서 X-Basic-User 를 ""(빈 값)으로 덮어써(제거) 클라이언트 위조를 차단한다.
        #   따라서 X-Basic-User 는 '이 핸들러 안에서만' 신뢰할 수 있다.
        #   CRITICAL: X-Basic-User 는 코드베이스 전체에서 반드시 이 /manage 핸들러에서만 읽는다(다른 어떤 곳에서도 읽지 않는다).
        if path == "/manage":
            # 헤더 접근은 대소문자 무시(email.message 기반) — Nginx 가 어떤 케이스로 보내든 매칭된다.
            basic_user = self.headers.get("X-Basic-User", "")
            if not basic_user or basic_user not in config.DASHBOARD_ADMINS:
                # 빈 값이거나 관리자 명단(config.DASHBOARD_ADMINS)에 없음
                # → 세션을 만들지 않고, 리다이렉트도 하지 않고 403.
                self.send_response(403)
                self.end_headers()
                return
            # POST /api/login 성공과 '동일한' 세션 생성 + 쿠키 설정 경로를 그대로 재사용한다:
            #   - 토큰 생성: secrets.token_hex(32)                          (via _create_session)
            #   - 세션 저장소: sessions[token] = {"user": <name>, "expires": ...}  (via _create_session)
            #   - Set-Cookie: HttpOnly; SameSite=Strict + X-Forwarded-Proto 기반 조건부 Secure (login 코드와 동일)
            # 이렇게 심은 세션은 기존 is_admin 판정(user in config.DASHBOARD_ADMINS)을 그대로 통과하므로
            # SPA 는 /api/session 을 통해 자동으로 admin 모드로 로드된다(별도 로그인 폼 불필요).
            token = _create_session(basic_user)
            # 로그인 이벤트 기록(감사 로그) — POST /api/login 경로와 동일하게 남긴다.
            _log_event("login", basic_user, self._get_client_ip(), self.headers.get("User-Agent", ""))
            self.send_response(302)
            # X-Forwarded-Proto(Nginx가 전달) 기반으로 HTTPS 판단 → Secure 플래그 조건부 부여 (login 과 동일)
            forwarded_proto = self.headers.get("X-Forwarded-Proto", "").lower()
            secure_flag = "; Secure" if forwarded_proto == "https" else ""
            self.send_header("Set-Cookie", f"session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_TTL}{secure_flag}")
            self.send_header("Location", "/")
            self.end_headers()
            return

        # 정적 파일 서빙
        if path == "/" or path == "":
            path = "/index.html"

        # 대시보드 진입 페이지 서빙 = "사람이 대시보드를 열었다" 신호 → 방문 기록 (봇 제외).
        if path == "/index.html":
            self._maybe_record_visit()

        # 보안: dashboard 디렉토리 내부만 서빙 (../ 트래버설 차단)
        file_path = os.path.normpath(os.path.join(DASHBOARD_DIR, path.lstrip("/")))
        if not file_path.startswith(os.path.normpath(DASHBOARD_DIR)):
            self.send_response(403)
            self.end_headers()
            return

        # 보안: 웹 자산 확장자 화이트리스트 — 그 외(*.py 등)는 404 (소스 노출 차단).
        # 예: GET /server.py → 여기서 404 (소스가 절대 반환되지 않음).
        ext = os.path.splitext(file_path)[1].lower()
        if ext not in _ALLOWED_STATIC_EXT:
            self.send_response(404)
            self.end_headers()
            return

        if not os.path.isfile(file_path):
            self.send_response(404)
            self.end_headers()
            return

        # MIME 타입 (ext는 위 화이트리스트 검사에서 이미 계산됨)
        mime = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".mjs": "application/javascript",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".gif": "image/gif",
            ".webp": "image/webp",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
            ".woff": "font/woff",
            ".ttf": "font/ttf",
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
        # 피드백 제출은 익명 게스트도 도달 가능 → 과대 payload를 read로 버퍼링하기 전에 먼저 차단
        if path == "/api/feedback" and content_len > MAX_FEEDBACK_BODY_LEN:
            self._send_json({"error": "payload too large"}, 413)
            return
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
                login_payload = {
                    "ok": True,
                    "user": username,
                    "is_admin": username in config.DASHBOARD_ADMINS,
                }
                login_payload.update(_build_user_payload(username))
                self.wfile.write(json.dumps(login_payload).encode())
            else:
                _record_attempt(ip, False)
                self._send_json({"error": "invalid credentials"}, 401)
            return

        # 피드백 제출 (익명 게스트 허용 — 로그인 사용자는 username 기록, 비로그인은 익명으로 저장)
        if path == "/api/feedback":
            username = ""
            token = self._get_session_token()
            # 유효 세션이면 사용자명 기록, 아니면 401 없이 익명으로 진행(슬라이딩 갱신은 _validate_session이 수행)
            if token and _validate_session(token):
                sess = sessions.get(token)
                username = sess.get("user", "") if sess else ""
            try:
                data = json.loads(body)
            except (json.JSONDecodeError, ValueError):
                self._send_json({"error": "bad request"}, 400)
                return

            # 허니팟: 숨김 필드가 채워져 있으면 봇으로 간주 — 저장 없이 성공으로 위장 응답
            honeypot = str(data.get("hp", "") or data.get("website", "")).strip()
            if honeypot:
                self._send_json({"ok": True})
                return

            client_ip = self._get_client_ip()
            if _feedback_rate_limited(client_ip):
                self._send_json({"error": "rate_limited"}, 429)
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
                    username=username,
                    ip=client_ip,
                    category=category,
                    message=message,
                    user_agent=self.headers.get("User-Agent", ""),
                )
                self._send_json({"ok": True, "id": fid})
            except Exception as e:
                logger.exception("feedback insert failed")
                self._send_json({"error": "internal server error"}, 500)
            return

        # 피드백 읽음 토글 (관리자 전용)
        if path == "/api/feedback/mark_read":
            token = self._get_session_token()
            # admin 보호 패턴: 세션 무효면 401, 유효하나 admin 아니면 403 (_validate_session이 슬라이딩 갱신도 수행)
            if not token or not _validate_session(token):
                self._send_json({"error": "unauthorized"}, 401)
                return
            sess = sessions.get(token)
            if not sess or sess.get("user", "") not in config.DASHBOARD_ADMINS:
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

        # API: 전체 월 강제 재조회 (관리자 전용)
        # 과거 월 논문의 Notion 수동 편집(GPT Reason 등)을 CSV 캐시에 반영하기 위한 수동 트리거.
        if path == "/api/refresh_all":
            token = self._get_session_token()
            # admin 보호 패턴: 세션 무효면 401, 유효하나 admin 아니면 403 (_validate_session이 슬라이딩 갱신도 수행)
            if not token or not _validate_session(token):
                self._send_json({"error": "unauthorized"}, 401)
                return
            sess = sessions.get(token)
            if not sess or sess.get("user", "") not in config.DASHBOARD_ADMINS:
                self._send_json({"error": "forbidden"}, 403)
                return
            try:
                from datetime import date as _date
                from analytics.notion_fetcher import fetch_papers
                # 프로젝트 시작 월 ~ 현재 월 전체 재조회 (force_refresh=True)
                # 시작 월: 환경변수 REFRESH_ALL_START_MONTH, 기본 2026-04
                start = os.getenv("REFRESH_ALL_START_MONTH", "2026-04")
                end = _date.today().strftime("%Y-%m")
                logger.info("[refresh_all] 시작: %s ~ %s (by %s)",
                            start, end, sess.get("user"))
                df = fetch_papers(start, end, force_refresh=True)
                # 실제 재조회된 월 목록: 결과 데이터에 존재하는 월(빈 월은 반영되지 않음)
                refreshed_months = sorted(
                    df["date"].dt.strftime("%Y-%m").dropna().unique().tolist()
                )
                logger.info("[refresh_all] 완료: %d건, %d개월", len(df), len(refreshed_months))
                self._send_json({
                    "ok": True,
                    "refreshed_months": refreshed_months,
                    "message": f"{len(refreshed_months)}개월 재조회 완료 ({len(df)}건)",
                })
            except Exception as e:
                # 스택트레이스는 서버 로그에만 남기고, 클라이언트에는 사람이 읽을 메시지만 전달
                logger.exception("refresh_all failed")
                self._send_json({"ok": False, "error": f"재조회 실패: {str(e)[:200]}"}, 500)
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
    if getattr(config, "VISIT_HASH_SALT_IS_EPHEMERAL", False):
        logger.warning(
            "VISIT_HASH_SALT 미설정 — 임시(프로세스 한정) salt 사용 중. "
            ".env에 안정적인 VISIT_HASH_SALT를 설정하세요. "
            "(미설정 시 재시작마다 방문자 total/누적 지표가 초기화됩니다.)"
        )

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
