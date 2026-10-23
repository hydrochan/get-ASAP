"""get-ASAP 정본 저장소 (SQLite) - Notion 탈출 설계(docs/notion-exit-design.md) §1·§2·§4.

정본은 VM의 SQLite(`data/asap.db`, 환경변수 ASAP_DB_PATH)이고, 대시보드가 서빙하는
`cache/papers_YYYY-MM.csv`는 이 DB에서 export한 스냅샷이다. Notion은 읽지도 쓰지도 않는다
(이 모듈과 운영 경로 어디에서도 Notion을 import하지 않는다).

핵심 규약:
- 쓰기는 모두 `BEGIN IMMEDIATE`의 짧은 트랜잭션이다(WAL, busy_timeout=10s, foreign_keys=ON).
- change_seq 부여 규칙은 `_bump` / `_insert_paper` / `_update_paper` **이 한 곳에서만** 강제한다.
  추적 열(TRACKED) 값이 실제로 바뀔 때만 `meta.last_seq`를 +1 해서 그 행에 준다.
- 중복 키는 전역·전 기간의 `title_key`(정규화 제목) 하나다. 먼저 들어온 행이 남는다.
- 행은 물리 삭제하지 않는다(tombstone = deleted_at). tombstone도 title_key를 점유한다.
- CSV export는 기존 `analytics/notion_fetcher._save_cache`와 바이트 호환이다.

CLI:
  python store.py --init      # 빈 DB 생성 + 스키마 초기화(이미 있으면 그대로)
"""
from __future__ import annotations

import contextlib
import csv
import datetime as _dt
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
import uuid

import config  # noqa: F401  (.env 로드 목적)
from excluded_journals import is_excluded_journal

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(PROJECT_ROOT, "cache")

SCHEMA_VERSION = 1
STATUSES = ("대기중", "관련", "불확실", "다운완료", "무관", "실패")
STATUS_SOURCES = ("get_asap", "paper_autodown", "notion_migrated", "human")
DEFAULT_STATUS = "대기중"
FIRST_BUCKET_MONTH = "2026-04"  # 이 달 이전 발행월은 이번 달 버킷으로 폴백(기존 resolve_db_for_month 재현)
GPT_REASON_MAX = 4000  # 서버 측 상한(클라이언트는 1900자로 자름)
CSV_COLUMNS = ["title", "journal", "date", "url", "status", "gpt_reason"]  # _save_cache와 동일
# 변경 피드(change_seq)를 올리는 추적 열. 이 열이 실제로 바뀔 때만 seq가 증가한다.
TRACKED = ("title", "journal", "pub_date", "url", "status", "gpt_reason", "deleted_at", "dup_of")

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


# ---------- 경로 / 시각 ----------

def db_path() -> str:
    """정본 DB 경로. ASAP_DB_PATH(상대경로면 프로젝트 루트 기준), 기본값 data/asap.db."""
    raw = os.getenv("ASAP_DB_PATH", "").strip()
    if not raw:
        return os.path.join(PROJECT_ROOT, "data", "asap.db")
    return raw if os.path.isabs(raw) else os.path.join(PROJECT_ROOT, raw)


def backup_dir(path: str | None = None) -> str:
    """gzip 백업 디렉터리: DB 파일과 같은 폴더의 backups/ (기본 data/backups/)."""
    return os.path.join(os.path.dirname(os.path.abspath(path or db_path())), "backups")


def _now() -> str:
    """ISO UTC 시각(`...Z`). 테스트에서 monkeypatch 한다."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _today() -> _dt.date:
    return _dt.date.today()


# ---------- 연결 / 트랜잭션 ----------

def connect(path: str | None = None, *, create: bool = False) -> sqlite3.Connection:
    """DB 연결. create=False(기본)에서 파일이 없으면 FileNotFoundError.

    빈 DB가 조용히 생기면 정본이 둘로 갈라지므로, 생성은 명시적으로(create=True) 요청한 경우만 한다.
    autocommit 모드(isolation_level=None)이며 트랜잭션은 write_tx/read_tx로 직접 연다.
    """
    path = path or db_path()
    if create:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    elif not os.path.exists(path):
        raise FileNotFoundError(path)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=10000")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


@contextlib.contextmanager
def write_tx(conn: sqlite3.Connection):
    """쓰기 트랜잭션(BEGIN IMMEDIATE). 예외가 나면 전부 롤백한다."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


@contextlib.contextmanager
def read_tx(conn: sqlite3.Connection):
    """읽기 스냅샷 트랜잭션. 안에서 읽은 모든 SELECT가 같은 스냅샷을 본다."""
    conn.execute("BEGIN")
    try:
        yield conn
    finally:
        if conn.in_transaction:
            conn.execute("ROLLBACK")


# ---------- 스키마 ----------

def _sql_in(values) -> str:
    return ", ".join("'" + v + "'" for v in values)


_DDL = [
    f"""
    CREATE TABLE IF NOT EXISTS papers (
        id INTEGER PRIMARY KEY,
        ref TEXT NOT NULL UNIQUE,
        title TEXT NOT NULL,
        title_key TEXT NOT NULL,
        journal TEXT NOT NULL DEFAULT '',
        url TEXT NOT NULL DEFAULT '',
        pub_date TEXT CHECK (pub_date IS NULL OR pub_date GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
        bucket_month TEXT NOT NULL CHECK (bucket_month GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]'),
        status TEXT NOT NULL DEFAULT '{DEFAULT_STATUS}' CHECK (status IN ({_sql_in(STATUSES)})),
        gpt_reason TEXT NOT NULL DEFAULT '',
        status_source TEXT NOT NULL CHECK (status_source IN ({_sql_in(STATUS_SOURCES)})),
        status_seq INTEGER,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        change_seq INTEGER NOT NULL UNIQUE,
        deleted_at TEXT,
        dup_of INTEGER REFERENCES papers(id),
        notion_last_edited TEXT
    )
    """,
    # 중복 키: dup_of가 없는 행끼리만 title_key 유일. tombstone(deleted_at)도 포함하므로 정리한 논문은 재유입되지 않는다.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_title_key ON papers(title_key) WHERE dup_of IS NULL",
    # bucket별 export·dirty 판정용 (change_seq 단독 조회는 UNIQUE 제약의 자동 인덱스가 맡는다)
    "CREATE INDEX IF NOT EXISTS ix_papers_bucket ON papers(bucket_month, change_seq)",
    """
    CREATE TABLE IF NOT EXISTS status_history (
        id INTEGER PRIMARY KEY,
        paper_id INTEGER NOT NULL REFERENCES papers(id),
        old_status TEXT,
        new_status TEXT NOT NULL,
        old_source TEXT,
        new_source TEXT NOT NULL,
        seq INTEGER NOT NULL,
        at TEXT NOT NULL
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_status_history_paper ON status_history(paper_id, seq)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]


def init_schema(conn: sqlite3.Connection) -> None:
    """스키마 생성(멱등). 더 새로운 schema_version의 DB면 거부한다."""
    with write_tx(conn):
        for stmt in _DDL:
            conn.execute(stmt)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('schema_version', ?)", (str(SCHEMA_VERSION),))
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('last_seq', '0')")
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('db_epoch', ?)", (_new_epoch(),))
        version = int(conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0])
    if version > SCHEMA_VERSION:
        raise RuntimeError(f"DB schema_version={version} 이 코드({SCHEMA_VERSION})보다 새롭습니다.")


def _new_epoch() -> str:
    return str(uuid.uuid4())


def db_epoch(conn: sqlite3.Connection) -> str:
    """이 DB 인스턴스의 식별자(meta.db_epoch). 백업 복원 때 backup_db.py 가 새로 발급하므로, 클라이언트는
    값이 바뀐 것으로 '서버가 되돌려졌다'(seq 번호가 재사용될 수 있다)를 알아챈다. 구 DB에 없으면 만들어 둔다."""
    row = conn.execute("SELECT value FROM meta WHERE key='db_epoch'").fetchone()
    if row is None:
        with write_tx(conn):
            conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES('db_epoch', ?)", (_new_epoch(),))
        row = conn.execute("SELECT value FROM meta WHERE key='db_epoch'").fetchone()
    return row[0]


def regenerate_epoch(conn: sqlite3.Connection) -> str:
    """db_epoch 를 새 값으로 바꾼다(복원 직후 호출). 새 값을 반환."""
    epoch = _new_epoch()
    with write_tx(conn):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES('db_epoch', ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (epoch,),
        )
    return epoch


def init_db(path: str | None = None) -> str:
    """빈 DB를 만들고 스키마를 초기화한다(멱등). 경로를 반환."""
    path = path or db_path()
    conn = connect(path, create=True)
    try:
        init_schema(conn)
    finally:
        conn.close()
    return path


def require_db(path: str | None = None) -> None:
    """운영 실행 전 점검: DB 파일이 있고 스키마가 초기화되어 있어야 한다. 아니면 예외."""
    conn = connect(path)
    try:
        row = conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()
    except sqlite3.DatabaseError as e:
        raise RuntimeError(f"정본 DB 스키마가 초기화되지 않았습니다: {e}") from e
    finally:
        conn.close()
    if row is None or int(row[0]) != SCHEMA_VERSION:
        raise RuntimeError("정본 DB schema_version이 맞지 않습니다.")


# ---------- 키 / 날짜 규칙 ----------

def normalize_title(title: str) -> str:
    """중복 판정 키(title_key). notion_client_mod._normalize_title_for_duplicate와 동일 규칙:
    NFKC, 대시류 통일, 공백 정리, casefold. (Notion 모듈을 import하지 않으려고 복사했고 테스트로 동치를 보증한다.)"""
    normalized = unicodedata.normalize("NFKC", title or "")
    normalized = normalized.replace("‐", "-").replace("‑", "-").replace("‒", "-")
    normalized = normalized.replace("–", "-").replace("—", "-").replace("−", "-")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized.strip().casefold()


def normalize_pub_date(value) -> str | None:
    """발행일 문자열을 `YYYY-MM-DD`로. 앞 10자가 실제 달력 날짜가 아니면 None."""
    s = (value or "").strip()[:10] if isinstance(value, str) or value is None else ""
    if not _DATE_RE.match(s):
        return None
    try:
        _dt.date.fromisoformat(s)
    except ValueError:
        return None
    return s


def bucket_month_for(pub_date, today: _dt.date | None = None) -> str:
    """신규 행의 bucket_month. 발행월이 `2026-04 <= 발행월 <= 이번 달`이면 발행월, 아니면 이번 달.

    기존 resolve_db_for_month의 동작(과거월 DB가 없으면 이번 달로 폴백, 미래·해석불가는 이번 달)을 재현한 것.
    """
    this_month = (today or _today()).strftime("%Y-%m")
    candidate = (pub_date or "")[:7] if isinstance(pub_date, str) or pub_date is None else ""
    if _MONTH_RE.match(candidate) and FIRST_BUCKET_MONTH <= candidate <= this_month:
        return candidate
    return this_month


# ---------- change_seq 부여 (이 구간이 유일한 강제 지점) ----------

def last_seq(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT value FROM meta WHERE key='last_seq'").fetchone()[0])


def _bump(conn: sqlite3.Connection) -> int:
    """meta.last_seq를 +1 하고 새 값을 반환. 반드시 write_tx 안에서만 호출된다."""
    conn.execute("UPDATE meta SET value = value + 1 WHERE key='last_seq'")
    return last_seq(conn)


def _insert_paper(conn: sqlite3.Connection, rec: dict, *, allow_dup: bool = False) -> int | None:
    """행 삽입 + seq 부여. title_key가 이미 점유(tombstone 포함)되어 있으면:
    allow_dup=False -> None 반환(스킵, seq 소비 없음) / allow_dup=True -> dup_of에 기존 행을 달아 삽입(이관용).
    """
    title_key = normalize_title(rec["title"])
    owner = conn.execute(
        "SELECT id FROM papers WHERE title_key=? AND dup_of IS NULL", (title_key,)
    ).fetchone()
    if owner is not None and not allow_dup:
        return None
    seq = _bump(conn)
    now = _now()
    cur = conn.execute(
        """
        INSERT INTO papers (ref, title, title_key, journal, url, pub_date, bucket_month, status, gpt_reason,
                            status_source, status_seq, created_at, updated_at, change_seq, dup_of, notion_last_edited)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(title_key) WHERE dup_of IS NULL DO NOTHING
        """,
        (
            rec["ref"], rec["title"], title_key, rec.get("journal") or "", rec.get("url") or "",
            rec.get("pub_date"), rec["bucket_month"], rec.get("status") or DEFAULT_STATUS,
            rec.get("gpt_reason") or "", rec["status_source"], seq,
            rec.get("created_at") or now, rec.get("updated_at") or now, seq,
            owner["id"] if owner is not None else None, rec.get("notion_last_edited"),
        ),
    )
    if cur.rowcount == 0:  # 위 선확인 때문에 도달 불가 - 방어용
        return None
    return cur.lastrowid


def _update_paper(
    conn: sqlite3.Connection,
    row: sqlite3.Row,
    changes: dict,
    *,
    now: str | None = None,
    status_source: str | None = None,
    extra: dict | None = None,
) -> bool:
    """추적 열이 실제로 바뀐 경우에만 UPDATE + seq 부여. 바뀐 게 없으면 아무것도 쓰지 않고 False.

    - status가 바뀌면 status_seq=새 seq, status_source=인자값(필수)으로 갱신하고 status_history에 남긴다.
    - extra는 비추적 열(예: notion_last_edited)로, 추적 열 변경이 있을 때만 함께 기록된다.
    """
    diff = {c: v for c, v in changes.items() if c in TRACKED and v != row[c]}
    if not diff:
        return False
    now = now or _now()
    seq = _bump(conn)
    sets = dict(diff)
    sets["change_seq"] = seq
    sets["updated_at"] = now
    if "title" in diff:
        sets["title_key"] = normalize_title(diff["title"])
    if "status" in diff:
        if status_source is None:
            raise ValueError("status 변경에는 status_source가 필요합니다.")
        sets["status_seq"] = seq
        sets["status_source"] = status_source
        conn.execute(
            "INSERT INTO status_history (paper_id, old_status, new_status, old_source, new_source, seq, at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (row["id"], row["status"], diff["status"], row["status_source"], status_source, seq, now),
        )
    for k, v in (extra or {}).items():
        sets[k] = v
    cols = list(sets)  # 열 이름은 위에서 고정된 화이트리스트(TRACKED/내부 열/extra 호출부 상수)뿐이다
    conn.execute(
        f"UPDATE papers SET {', '.join(c + '=?' for c in cols)} WHERE id=?",
        [sets[c] for c in cols] + [row["id"]],
    )
    return True


# ---------- Gmail 수집 경로 ----------

def save_papers(papers, conn: sqlite3.Connection | None = None, today: _dt.date | None = None) -> dict:
    """논문 배치 저장(메일 1통 = 한 트랜잭션). 반환: {"saved": N, "skipped": M, "failed": K}.

    기존 notion_client_mod.save_papers와 같은 반환 계약. 중복(title_key 점유, tombstone 포함)은
    skipped, 개별 행 제약 위반은 failed로 센다. DB 수준 오류(잠금·디스크 등)는 예외로 올라가고
    그 메일 전체가 롤백된다(main.py가 메일 재시도 대기로 돌린다).
    """
    result = {"saved": 0, "skipped": 0, "failed": 0}
    if not papers:
        return result
    own = conn is None
    if own:
        conn = connect()
    try:
        with write_tx(conn):
            now = _now()
            for paper in papers:
                title = paper.title or ""
                if not normalize_title(title):
                    logger.warning("빈 제목 논문 스킵 (journal=%s)", paper.journal)
                    result["skipped"] += 1
                    continue
                rec = {
                    "ref": str(uuid.uuid4()),
                    "title": title,
                    "journal": paper.journal or "",
                    "url": paper.url or "",
                    "pub_date": normalize_pub_date(paper.date),
                    "bucket_month": bucket_month_for(paper.date, today),
                    "status": DEFAULT_STATUS,
                    "gpt_reason": "",
                    "status_source": "get_asap",
                    "created_at": now,
                    "updated_at": now,
                }
                conn.execute("SAVEPOINT paper_row")
                try:
                    inserted = _insert_paper(conn, rec)
                    conn.execute("RELEASE paper_row")
                except sqlite3.IntegrityError as e:
                    conn.execute("ROLLBACK TO paper_row")
                    conn.execute("RELEASE paper_row")
                    logger.warning("논문 저장 실패 (제약 위반): %s / %s", title[:60], e)
                    result["failed"] += 1
                    continue
                if inserted is None:
                    logger.info("중복 스킵: %s", title[:60])
                    result["skipped"] += 1
                else:
                    result["saved"] += 1
    finally:
        if own:
            conn.close()
    return result


def upsert_migrated(conn: sqlite3.Connection, rec: dict, now: str | None = None) -> str:
    """Notion 이관 행을 ref 기준으로 upsert (write_tx 안에서 호출). 반환: inserted/updated/unchanged/protected.

    - 신규 ref: status_source=notion_migrated로 삽입. title_key가 이미 있으면 모두 보존하되 dup_of를 단다.
    - 기존 ref: status_source가 notion_migrated인 행만 갱신(그 외는 이후 수정을 보호하려고 'protected'로 건너뜀).
    rec 필수 키: ref, title, bucket_month, created_at. 선택: journal, url, pub_date, status, gpt_reason, notion_last_edited.
    """
    row = conn.execute("SELECT * FROM papers WHERE ref=?", (rec["ref"],)).fetchone()
    if row is None:
        full = dict(rec, status_source="notion_migrated", updated_at=rec.get("notion_last_edited") or rec["created_at"])
        _insert_paper(conn, full, allow_dup=True)
        return "inserted"
    if row["status_source"] != "notion_migrated":
        return "protected"
    changes = {
        "title": rec["title"],
        "journal": rec.get("journal") or "",
        "url": rec.get("url") or "",
        "pub_date": rec.get("pub_date"),
        "status": rec.get("status") or DEFAULT_STATUS,
        "gpt_reason": rec.get("gpt_reason") or "",
    }
    new_key = normalize_title(rec["title"])
    if new_key != row["title_key"]:
        # 제목이 바뀌어 키가 달라진 경우: 새 키의 주인이 따로 있으면 dup, 없으면 정본이 된다.
        other = conn.execute(
            "SELECT id FROM papers WHERE title_key=? AND dup_of IS NULL AND id != ?", (new_key, row["id"])
        ).fetchone()
        changes["dup_of"] = other["id"] if other is not None else None
    changed = _update_paper(
        conn, row, changes, now=now, status_source="notion_migrated",
        extra={"notion_last_edited": rec.get("notion_last_edited")},
    )
    return "updated" if changed else "unchanged"


# ---------- 동기화 API용: 변경 피드 / 판정 ----------

def row_to_feed(r: sqlite3.Row) -> dict:
    """변경 피드 한 행(§5 계약 필드)."""
    return {
        "ref": r["ref"],
        "title": r["title"],
        "journal": r["journal"],
        "pub_date": r["pub_date"],
        "url": r["url"],
        "status": r["status"],
        "gpt_reason": r["gpt_reason"],
        "status_source": r["status_source"],
        "status_seq": r["status_seq"],
        "created_at": r["created_at"],
        "bucket_month": r["bucket_month"],
        "deleted": r["deleted_at"] is not None,
        "dup_of_ref": r["dup_of_ref"],
        "seq": r["change_seq"],
    }


_FEED_SELECT = (
    "SELECT p.*, d.ref AS dup_of_ref FROM papers p LEFT JOIN papers d ON d.id = p.dup_of"
)


def fetch_changes(conn: sqlite3.Connection, after: int, limit: int = 500) -> dict:
    """`change_seq > after` 행을 seq 순으로 limit개. 한 번의 읽기 스냅샷에서 읽는다.

    각 행은 자신의 최신 seq 자리에 한 번만 나온다(나중에 바뀐 행은 더 큰 seq를 받으므로 누락 없이 최소 1회 전달).
    응답: rows, next_after, has_more (+ last_seq·db_epoch: 클라이언트가 서버가 복원·초기화됐음을 알아채는 용도).
    """
    epoch = db_epoch(conn)  # 구 DB면 여기서 만든다(쓰기) - 읽기 스냅샷 밖에서
    with read_tx(conn):
        got = conn.execute(
            f"{_FEED_SELECT} WHERE p.change_seq > ? ORDER BY p.change_seq LIMIT ?", (after, limit + 1)
        ).fetchall()
        server_seq = last_seq(conn)
    has_more = len(got) > limit
    got = got[:limit]
    rows = [row_to_feed(r) for r in got]
    return {
        "rows": rows,
        "next_after": rows[-1]["seq"] if rows else after,
        "has_more": has_more,
        "last_seq": server_seq,
        "db_epoch": epoch,
    }


def _current_feed_row(conn: sqlite3.Connection, ref: str) -> dict:
    return row_to_feed(conn.execute(f"{_FEED_SELECT} WHERE p.ref = ?", (ref,)).fetchone())


def apply_verdicts(conn: sqlite3.Connection, items: list, seen_through: int) -> dict:
    """paper_autodown의 판정 배치 적용(배치 1개 = 트랜잭션 1개, 집합 의미라 재시도해도 결과가 같다).

    항목별 결과: updated / unchanged / gone(모르는 ref·tombstone) / conflict / invalid.
    conflict: status_source='human' AND status_seq > seen_through AND 요청 status != 현재 status
              -> 적용하지 않고 current를 돌려준다(2단계 수동 판정 보호).
    status가 실제로 바뀌면 status_source=paper_autodown, status_seq=새 seq. gpt_reason은 4000자로 자른다.
    status와 gpt_reason이 모두 null인 항목은 할 일이 없으므로 unchanged다.
    """
    results = []
    with write_tx(conn):
        now = _now()
        for item in items:
            results.append(_apply_one_verdict(conn, item, seen_through, now))
        seq = last_seq(conn)
    return {"results": results, "last_seq": seq}


def _apply_one_verdict(conn: sqlite3.Connection, item, seen_through: int, now: str) -> dict:
    ref = item.get("ref") if isinstance(item, dict) else None
    if not isinstance(ref, str) or not ref:
        return {"ref": ref if isinstance(ref, str) else None, "result": "invalid"}
    status = item.get("status")
    reason = item.get("gpt_reason")
    if (status is not None and status not in STATUSES) or (reason is not None and not isinstance(reason, str)):
        return {"ref": ref, "result": "invalid"}
    row = conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()
    if row is None or row["deleted_at"] is not None:
        return {"ref": ref, "result": "gone"}
    if status is None and reason is None:
        return {"ref": ref, "result": "unchanged"}
    if (
        status is not None
        and status != row["status"]
        and row["status_source"] == "human"
        and row["status_seq"] is not None
        and row["status_seq"] > seen_through
    ):
        return {"ref": ref, "result": "conflict", "current": _current_feed_row(conn, ref)}
    changes = {}
    if status is not None:
        changes["status"] = status
    if reason is not None:
        changes["gpt_reason"] = reason[:GPT_REASON_MAX]
    # 판정이 무엇이든(gpt_reason만 바뀌어도) 이관 행이 paper_autodown 의 손을 탄 것이므로 출처를 넘긴다 -
    # 안 그러면 이관 재실행(upsert_migrated)이 이 행을 notion_migrated 로 보고 판정 사유를 덮어쓴다.
    # human 은 건드리지 않는다. status_seq 는 status 가 바뀔 때만 _update_paper 가 올린다.
    extra = {"status_source": "paper_autodown"} if row["status_source"] == "notion_migrated" else None
    changed = _update_paper(conn, row, changes, now=now, status_source="paper_autodown", extra=extra)
    return {"ref": ref, "result": "updated" if changed else "unchanged"}


# ---------- CSV export (_save_cache 바이트 호환) ----------

def csv_path(month: str, cache_dir: str | None = None) -> str:
    return os.path.join(cache_dir or CACHE_DIR, f"papers_{month}.csv")


def _check_month(month: str) -> str:
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError(f"월 형식이 잘못되었습니다(YYYY-MM): {month!r}")
    return month


def _csv_rows(conn: sqlite3.Connection, month: str) -> tuple[list[list[str]], int, int]:
    """(행 목록, 읽은 시점 max change_seq, 제외 저널로 드롭한 건수). 한 읽기 스냅샷.

    정렬은 발행일 오름차순(없는 날짜는 끝), 같으면 id. tombstone은 제외, 이관 중복 행은 포함한다
    (기존 CSV에도 들어 있던 행들이므로).
    """
    with read_tx(conn):
        seq = conn.execute(
            "SELECT COALESCE(MAX(change_seq), 0) FROM papers WHERE bucket_month=?", (month,)
        ).fetchone()[0]
        got = conn.execute(
            "SELECT title, journal, pub_date, url, status, gpt_reason FROM papers "
            "WHERE bucket_month=? AND deleted_at IS NULL ORDER BY pub_date IS NULL, pub_date, id",
            (month,),
        ).fetchall()
    rows, dropped = [], 0
    for r in got:
        if is_excluded_journal(r["journal"]):
            dropped += 1
            continue
        rows.append([r["title"], r["journal"], r["pub_date"] or "", r["url"], r["status"], r["gpt_reason"]])
    return rows, seq, dropped


def _write_csv_atomic(path: str, rows: list[list[str]]) -> None:
    """임시 파일에 쓰고 os.replace로 교체(서버의 mtime 메모가 그대로 동작). 형식은 _save_cache와 같다:
    QUOTE_ALL, utf-8-sig, 헤더 6열, 줄바꿈은 pandas 기본(os.linesep)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"  # 대시보드 스레드끼리 같은 월을 동시에 export 해도 충돌하지 않게
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator=os.linesep)
            writer.writerow(CSV_COLUMNS)
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


_LOCK_NAME = ".export.lock"
_LOCK_TIMEOUT_SECONDS = 120


def _lock_exclusive(fh) -> None:
    """파일 배타 잠금(블로킹). POSIX 는 flock, Windows 는 msvcrt.locking(1바이트, 재시도), 둘 다 없으면 no-op."""
    try:
        import fcntl
    except ImportError:
        fcntl = None
    if fcntl is not None:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        return
    try:
        import msvcrt
    except ImportError:
        return  # 잠금 수단이 없는 환경(테스트 전용) - MAX(csv_seq) 가 마지막 방어선이다
    deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
    while True:
        try:
            fh.seek(0)
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.05)


def _unlock(fh) -> None:
    try:
        import fcntl
    except ImportError:
        fcntl = None
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
            return
        import msvcrt

        fh.seek(0)
        msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
    except (ImportError, OSError):
        pass  # 파일을 닫으면 어차피 풀린다


@contextlib.contextmanager
def _export_lock(cache_dir: str | None = None):
    """월 export 한 건을 `cache/.export.lock` 배타 잠금으로 직렬화한다.

    대시보드 스레드·refresh_csv cron·main.py 가 같은 월을 동시에 export 할 때, 먼저 읽은(오래된) 스냅샷이
    나중에 CSV 와 csv_seq 를 덮어 최신 변경이 export_dirty 에서 영영 빠지는 경합을 막는다.
    """
    directory = cache_dir or CACHE_DIR
    os.makedirs(directory, exist_ok=True)
    fh = open(os.path.join(directory, _LOCK_NAME), "a+b")
    try:
        _lock_exclusive(fh)
        yield
    finally:
        _unlock(fh)
        fh.close()


def export_csv(
    month: str,
    conn: sqlite3.Connection | None = None,
    cache_dir: str | None = None,
    *,
    track: bool = True,
) -> int:
    """한 bucket을 cache/papers_<month>.csv로 export하고 쓴 행 수를 반환.

    track=True면 읽은 스냅샷의 max(change_seq)를 meta `csv_seq:<month>`에 기록한다(export_dirty 판정용).
    기록은 MAX(기존, 새 값)이라 값이 뒤로 가지 않는다. 읽기·쓰기·기록은 `cache/.export.lock` 으로 직렬화된다.
    검증용 임시 export는 track=False로 호출한다.
    """
    _check_month(month)
    own = conn is None
    if own:
        conn = connect()
    try:
        with _export_lock(cache_dir):
            rows, seq, dropped = _csv_rows(conn, month)
            if dropped:
                logger.info("제외 저널 %d건 CSV 캐시에서 드롭 (%s)", dropped, month)
            path = csv_path(month, cache_dir)
            _write_csv_atomic(path, rows)
            if track:
                with write_tx(conn):
                    conn.execute(
                        "INSERT INTO meta(key, value) VALUES(?, ?) "
                        "ON CONFLICT(key) DO UPDATE SET value = excluded.value "
                        "WHERE CAST(excluded.value AS INTEGER) > CAST(meta.value AS INTEGER)",
                        (f"csv_seq:{month}", str(seq)),
                    )
        logger.info("캐시 저장: %s (%d건)", path, len(rows))
        return len(rows)
    finally:
        if own:
            conn.close()


def _bucket_max_seqs(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        r[0]: r[1]
        for r in conn.execute("SELECT bucket_month, MAX(change_seq) FROM papers GROUP BY bucket_month")
    }


def export_dirty(conn: sqlite3.Connection | None = None, cache_dir: str | None = None) -> dict[str, int]:
    """max(change_seq) > csv_seq:<month> 인 bucket(또는 CSV 파일이 없는 bucket)만 export. {월: 행 수}.
    바뀐 게 없으면 아무것도 하지 않는다(cron 15분 간격 전제)."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with read_tx(conn):
            maxes = _bucket_max_seqs(conn)
            seen = {
                r[0][len("csv_seq:"):]: int(r[1])
                for r in conn.execute("SELECT key, value FROM meta WHERE key LIKE 'csv_seq:%'")
            }
        done = {}
        for month in sorted(maxes):
            if maxes[month] > seen.get(month, 0) or not os.path.exists(csv_path(month, cache_dir)):
                done[month] = export_csv(month, conn, cache_dir)
        return done
    finally:
        if own:
            conn.close()


def export_all(conn: sqlite3.Connection | None = None, cache_dir: str | None = None) -> dict[str, int]:
    """행이 있는 모든 bucket을 강제 export. {월: 행 수}."""
    own = conn is None
    if own:
        conn = connect()
    try:
        with read_tx(conn):
            months = sorted(_bucket_max_seqs(conn))
        return {m: export_csv(m, conn, cache_dir) for m in months}
    finally:
        if own:
            conn.close()


# ---------- 백업 ----------

def backup(dest: str, conn: sqlite3.Connection | None = None) -> str:
    """SQLite backup API로 일관된 사본을 dest에 만든다(임시 파일 -> 무결성 확인 -> os.replace).
    사본은 WAL이 아닌 단일 파일(journal_mode=DELETE)이다."""
    own = conn is None
    if own:
        conn = connect()
    tmp = f"{dest}.{os.getpid()}.tmp"
    os.makedirs(os.path.dirname(os.path.abspath(dest)), exist_ok=True)
    try:
        target = sqlite3.connect(tmp)
        try:
            conn.backup(target)
            target.execute("PRAGMA journal_mode=DELETE")
            if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise RuntimeError("백업 사본 무결성 검사 실패")
        finally:
            target.close()
        os.replace(tmp, dest)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise
    finally:
        if own:
            conn.close()
    return dest


if __name__ == "__main__":
    import sys

    if "--init" in sys.argv[1:]:
        print(f"DB 초기화 완료: {init_db()}")
    else:
        print(__doc__)
