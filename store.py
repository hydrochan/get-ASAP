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
import hashlib
import json
import logging
import os
import pathlib
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.parse
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
# 공개 슬림 CSV(`?fmt=slim`): 개인별 이메일 추적 리다이렉트 URL(바이트의 ~68%)을 빼고 id·doi 를 넣은 형식.
SLIM_COLUMNS = ["id", "title", "journal", "date", "doi", "gpt_reason"]
# 변경 피드(change_seq)를 올리는 추적 열. 이 열이 실제로 바뀔 때만 seq가 증가한다.
TRACKED = ("title", "journal", "pub_date", "url", "status", "gpt_reason", "deleted_at", "dup_of")
# AI 가 제목으로 표시한 글 종류(paper_autodown article_kind). 서버 내부용이라 TRACKED 에 넣지 않는다 -
# change_seq 를 올리지 않고 변경 피드·공개 CSV 의 열로도 나가지 않는다. `non_research`·`comment` 는 공개 화면에서 숨긴다
# (행은 지우지 않으므로 검토 탭에서 복원할 수 있다).
ARTICLE_KINDS = ("research", "review", "comment", "non_research")
HIDDEN_ARTICLE_KINDS = frozenset({"non_research", "comment"})
HUMAN_ARTICLE_KINDS = ("research", "non_research")  # 관리자가 지정할 수 있는 값: 복원(research) / 숨김(non_research)
ARTICLE_KIND_COLUMNS = ("ai_article_kind", "article_kind_source")  # source: 'ai' | 'human'(사람이 정하면 AI 가 덮어쓰지 않는다)

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

def connect(path: str | None = None, *, create: bool = False, readonly: bool = False) -> sqlite3.Connection:
    """DB 연결. create=False(기본)에서 파일이 없으면 FileNotFoundError.

    빈 DB가 조용히 생기면 정본이 둘로 갈라지므로, 생성은 명시적으로(create=True) 요청한 경우만 한다.
    autocommit 모드(isolation_level=None)이며 트랜잭션은 write_tx/read_tx로 직접 연다.
    readonly=True 는 `mode=ro` + `query_only` 연결(공개 읽기 경로용): journal_mode 등 DB 설정을 건드리지 않고
    어떤 쓰기도 SQLite 가 거부한다. create 와 함께 쓸 수 없다.
    """
    path = path or db_path()
    if readonly:
        if create:
            raise ValueError("readonly 연결은 create 와 함께 쓸 수 없습니다.")
        if not os.path.exists(path):
            raise FileNotFoundError(path)
        conn = sqlite3.connect(
            pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro", uri=True, timeout=10, isolation_level=None
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA query_only=ON")
        return conn
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


# title_key 에서 공백을 모두 뺀 값("co 2" -> "co2")에 거는 부분 인덱스. 위·아래첨자가 쪼개져 저장된 옛 제목("CO 2")과
# 고쳐진 파서의 제목("CO2")을 같은 논문으로 잡는 데 쓴다. 인덱스 식·조회 식은 이 표기 그대로여야 한다.
_TITLE_SQUASH_EXPR = "replace(title_key,' ','')"
_TITLE_SQUASH_INDEX_SQL = (
    f"CREATE INDEX IF NOT EXISTS ix_papers_title_squash ON papers({_TITLE_SQUASH_EXPR}) WHERE dup_of IS NULL"
)

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
        notion_last_edited TEXT,
        ai_article_kind TEXT,
        article_kind_source TEXT
    )
    """,
    # 중복 키: dup_of가 없는 행끼리만 title_key 유일. tombstone(deleted_at)도 포함하므로 정리한 논문은 재유입되지 않는다.
    "CREATE UNIQUE INDEX IF NOT EXISTS ux_papers_title_key ON papers(title_key) WHERE dup_of IS NULL",
    # 공백 무시 중복 조회용(_insert_paper): 쿼리의 식이 이 인덱스 식과 글자 그대로 같아야 인덱스를 탄다.
    _TITLE_SQUASH_INDEX_SQL,
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
        at TEXT NOT NULL,
        actor TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS ix_status_history_paper ON status_history(paper_id, seq)",
    "CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)",
]


def _ensure_history_actor(conn: sqlite3.Connection) -> None:
    """status_history.actor(판정한 관리자 ID) 열을 보장한다(가산·멱등). 검토 UI 이전에 만든 DB를 위한 것으로,
    write_tx 안에서 호출한다(BEGIN IMMEDIATE가 동시 ALTER를 직렬화). 사람 판정이 아닌 기존 경로는 이 열을 쓰지 않는다."""
    if "actor" not in [r[1] for r in conn.execute("PRAGMA table_info(status_history)")]:
        conn.execute("ALTER TABLE status_history ADD COLUMN actor TEXT")


def _ensure_title_squash_index(conn: sqlite3.Connection) -> None:
    """공백 무시 중복 조회 인덱스를 보장한다(멱등). write_tx 안에서 호출한다.
    운영 DB 는 init_schema 가 다시 불리지 않으므로 이 인덱스를 쓰는 저장 경로(save_papers)가 직접 부른다."""
    if conn.execute("SELECT 1 FROM sqlite_master WHERE type='index' AND name='ix_papers_title_squash'").fetchone() is None:
        conn.execute(_TITLE_SQUASH_INDEX_SQL)


def _missing_article_kind_columns(conn: sqlite3.Connection) -> list[str]:
    have = {r[1] for r in conn.execute("PRAGMA table_info(papers)")}
    return [c for c in ARTICLE_KIND_COLUMNS if c not in have]


def _ensure_article_kind_columns(conn: sqlite3.Connection) -> None:
    """papers.ai_article_kind / article_kind_source 열을 보장한다(가산·멱등, NULL 허용). write_tx 안에서 호출한다.
    운영 DB 는 init_schema 가 다시 불리지 않으므로 이 열을 읽거나 쓰는 경로(apply_verdicts·검토 API)가 직접 부른다."""
    for col in _missing_article_kind_columns(conn):
        conn.execute(f"ALTER TABLE papers ADD COLUMN {col} TEXT")


def _ensure_article_kind_columns_now(conn: sqlite3.Connection) -> None:
    """읽기 경로용: 열이 없을 때만 짧은 쓰기 트랜잭션으로 추가한다(이미 있으면 쓰기 없음)."""
    if _missing_article_kind_columns(conn):
        with write_tx(conn):
            _ensure_article_kind_columns(conn)


def init_schema(conn: sqlite3.Connection) -> None:
    """스키마 생성(멱등). 더 새로운 schema_version의 DB면 거부한다."""
    with write_tx(conn):
        for stmt in _DDL:
            conn.execute(stmt)
        _ensure_history_actor(conn)
        _ensure_article_kind_columns(conn)
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

    allow_dup=False 에서는 공백만 다른 키("CO2" vs 옛 파서가 쪼갠 "CO 2")도 같은 논문으로 보고 스킵한다
    (범위는 정확 키와 같다: 전 기간·dup_of IS NULL·tombstone 포함). allow_dup=True(이관) 동작은 그대로다.
    """
    title_key = normalize_title(rec["title"])
    owner = conn.execute(
        "SELECT id FROM papers WHERE title_key=? AND dup_of IS NULL", (title_key,)
    ).fetchone()
    if owner is None and not allow_dup:
        owner = conn.execute(
            f"SELECT id FROM papers WHERE {_TITLE_SQUASH_EXPR}=? AND dup_of IS NULL", (title_key.replace(" ", ""),)
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
    actor: str | None = None,
    claim_source: bool = False,
) -> bool:
    """추적 열이 실제로 바뀐 경우에만 UPDATE + seq 부여. 바뀐 게 없으면 아무것도 쓰지 않고 False.

    - status가 바뀌면 status_seq=새 seq, status_source=인자값(필수)으로 갱신하고 status_history에 남긴다.
    - extra는 비추적 열(예: notion_last_edited)로, 추적 열 변경이 있을 때만 함께 기록된다.
    - claim_source=True(사람 판정 전용): status가 그대로여도 status_source가 인자값과 다르면 그것을 '변경'으로 친다.
      status_source는 추적 열이 아니지만 변경 피드(change_seq)를 타지 않으면 paper_autodown이 "사람이 이 값을
      확인했다"는 사실을 영영 받지 못하고, 충돌 규칙(human AND status_seq > seen_through)도 이 확인을 보호하지
      못한다. 그래서 출처만 바뀌어도 seq를 올리고 status_seq=새 seq로 한다(seq 부여는 여전히 이 함수 한 곳).
    - actor: 판정한 사람(status_history.actor). None이면 열을 쓰지 않는다(열이 없는 구 DB도 그대로 동작).
    """
    diff = {c: v for c, v in changes.items() if c in TRACKED and v != row[c]}
    source_flip = claim_source and status_source is not None and status_source != row["status_source"]
    if not diff and not source_flip:
        return False
    now = now or _now()
    seq = _bump(conn)
    sets = dict(diff)
    sets["change_seq"] = seq
    sets["updated_at"] = now
    if "title" in diff:
        sets["title_key"] = normalize_title(diff["title"])
    if "status" in diff or source_flip:
        if status_source is None:
            raise ValueError("status 변경에는 status_source가 필요합니다.")
        sets["status_seq"] = seq
        sets["status_source"] = status_source
        hist_cols = ["paper_id", "old_status", "new_status", "old_source", "new_source", "seq", "at"]
        hist_vals = [row["id"], row["status"], diff.get("status", row["status"]), row["status_source"], status_source, seq, now]
        if actor is not None:
            hist_cols.append("actor")
            hist_vals.append(actor)
        conn.execute(
            f"INSERT INTO status_history ({', '.join(hist_cols)}) VALUES ({', '.join('?' * len(hist_cols))})", hist_vals
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
            _ensure_title_squash_index(conn)
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
    status와 gpt_reason이 모두 null이고 article_kind도 없는 항목은 할 일이 없으므로 unchanged다.

    선택 키 `article_kind`(ARTICLE_KINDS 중 하나): 없거나 null이면 손대지 않고, 네 값이 아닌 값이면 항목 전체가 invalid.
    판정 충돌 규칙과 무관하게 기록하고(conflict 항목에도), status·gpt_reason이 둘 다 null인 kind 전용 항목(백필)도 받는다.
    사람이 정한 값(article_kind_source='human')은 덮어쓰지 않는다. change_seq·status_seq는 올리지 않는다(내부용 표시).
    결과 어휘는 그대로다: kind 전용 항목은 저장된 값이 바뀌면 updated, 아니면 unchanged. status/reason과 함께 오면
    둘 중 하나라도 바뀌었을 때 updated.
    """
    results = []
    with write_tx(conn):
        _ensure_article_kind_columns(conn)
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
    kind = item.get("article_kind")
    if (
        (status is not None and status not in STATUSES)
        or (reason is not None and not isinstance(reason, str))
        or (kind is not None and kind not in ARTICLE_KINDS)
    ):
        return {"ref": ref, "result": "invalid"}
    row = conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()
    if row is None or row["deleted_at"] is not None:
        return {"ref": ref, "result": "gone"}
    # 글 종류 표시는 판정 충돌 규칙과 독립이다(판정이 아니라 표시이므로 conflict 항목에도 기록한다).
    kind_changed = _record_ai_article_kind(conn, row, kind) if kind is not None else False
    if status is None and reason is None:
        return {"ref": ref, "result": "updated" if kind_changed else "unchanged"}
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
    return {"ref": ref, "result": "updated" if (changed or kind_changed) else "unchanged"}


def _is_hidden_kind(kind) -> bool:
    return kind in HIDDEN_ARTICLE_KINDS


def _mark_csv_dirty(conn: sqlite3.Connection, month: str) -> None:
    """공개 CSV 에 보이는 행 집합이 바뀌었는데 change_seq 는 그대로일 때, 그 월을 다음 export_dirty 에서 다시 쓰게 한다.
    max(change_seq) > csv_seq 판정이 못 잡으므로 csv_seq 메모를 지운다(fix_future_dates 와 같은 방식). write_tx 안에서 호출."""
    conn.execute("DELETE FROM meta WHERE key=?", (f"csv_seq:{month}",))


def _record_ai_article_kind(conn: sqlite3.Connection, row: sqlite3.Row, kind: str) -> bool:
    """AI 가 표시한 글 종류를 기록한다(write_tx 안). 사람이 정한 값이면 건드리지 않는다. 저장된 값이 바뀌면 True.
    숨김 여부(HIDDEN_ARTICLE_KINDS)가 바뀌면 그 월 CSV 를 다시 쓰도록 표시한다. change_seq·updated_at 은 건드리지 않는다."""
    if row["article_kind_source"] == "human":
        return False
    old = row["ai_article_kind"]
    if old == kind and row["article_kind_source"] == "ai":
        return False
    conn.execute("UPDATE papers SET ai_article_kind=?, article_kind_source='ai' WHERE id=?", (kind, row["id"]))
    if _is_hidden_kind(old) != _is_hidden_kind(kind):
        _mark_csv_dirty(conn, row["bucket_month"])
    return old != kind


# ---------- 검토 UI(관리자): 목록 조회 / 사람 판정 (docs/review-ui-design.md §1) ----------

HUMAN_STATUSES = ("관련", "불확실", "무관", "다운완료")  # 사람이 지정할 수 있는 라벨(대기중·실패는 시스템 상태)
REVIEW_DEFAULT_STATUS = "불확실"
REVIEW_MAX_LIMIT = 500
REVIEW_MAX_Q = 200


def _review_row(r: sqlite3.Row) -> dict:
    """검토 목록 한 행(설계 §1 응답 필드). date는 pub_date(없으면 None)."""
    return {
        "ref": r["ref"],
        "title": r["title"],
        "journal": r["journal"],
        "date": r["pub_date"],
        "url": r["url"],
        "status": r["status"],
        "status_source": r["status_source"],
        "gpt_reason": r["gpt_reason"],
        "created_at": r["created_at"],
        "bucket_month": r["bucket_month"],
        "ai_article_kind": r["ai_article_kind"],
        "article_kind_source": r["article_kind_source"],
    }


REVIEW_KINDS = ("non_research", "comment", "review", "hidden")  # hidden = 공개 화면에서 숨겨지는 값(non_research 또는 comment)
_HIDDEN_KINDS_SQL = _sql_in(sorted(HIDDEN_ARTICLE_KINDS))


def list_review_papers(
    conn: sqlite3.Connection,
    status: str = REVIEW_DEFAULT_STATUS,
    month: str | None = None,
    q: str | None = None,
    limit: int = 100,
    offset: int = 0,
    kind: str | None = None,
) -> dict:
    """검토 목록. 한 읽기 스냅샷에서 {rows, total, counts, kind_counts, months}를 돌려준다.

    - 대상: tombstone(deleted_at)과 dup_of 행을 뺀 정본 행. created_at DESC(같으면 id DESC)로 정렬.
    - status: 라벨 하나 또는 "all". month: bucket_month(YYYY-MM). q: 제목·저널 부분일치(대소문자 무시).
    - kind: None(조건 없음) 또는 REVIEW_KINDS 중 하나(AI 가 표시한 글 종류). "hidden"은 non_research 또는 comment.
      이 목록은 공개 CSV 의 숨김 필터를 적용하지 않는다(숨겨진 행이 여기서 보이고 복원된다).
    - q 비교는 중복 키와 같은 정규화(NFKC·대시 통일·공백·casefold)를 양쪽에 적용한다(SQLite LIKE는 비ASCII 대소문자를 못 접는다).
    - counts: 같은 month·q·kind 조건에서 상태별 건수(여섯 라벨 모두, 0 포함). total: 선택한 status의 건수("all"이면 합계).
    - kind_counts: 같은 month·q 조건(status·kind와 무관)에서 {non_research, comment, review, hidden} 건수 - 칩 표시용.
    - months: month 조건과 무관하게 정본 행이 있는 bucket_month 전체(내림차순) - 월 선택 목록용.
    잘못된 값(status·month·limit·offset·kind)은 ValueError.
    """
    if status != "all" and status not in STATUSES:
        raise ValueError(f"status: {status!r}")
    if kind is not None and kind not in REVIEW_KINDS:
        raise ValueError(f"kind: {kind!r}")
    if month:
        _check_month(month)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= REVIEW_MAX_LIMIT:
        raise ValueError("limit")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValueError("offset")
    if q is not None and not isinstance(q, str):
        raise ValueError("q")
    base = "deleted_at IS NULL AND dup_of IS NULL"
    where, params = base, []
    if month:
        where += " AND bucket_month = ?"
        params.append(month)
    needle = normalize_title(q[:REVIEW_MAX_Q]) if q else ""
    if needle:
        conn.create_function("_fold", 1, normalize_title, deterministic=True)
        where += " AND (instr(title_key, ?) > 0 OR instr(_fold(journal), ?) > 0)"
        params += [needle, needle]
    _ensure_article_kind_columns_now(conn)  # 열 추가 전에 만든 운영 DB 를 위한 지연 마이그레이션 (읽기 스냅샷 밖에서)
    with read_tx(conn):
        kind_counts = {k: 0 for k in REVIEW_KINDS}
        for k, n in conn.execute(
            f"SELECT ai_article_kind, COUNT(*) FROM papers WHERE {where} "
            f"AND ai_article_kind IN ({_sql_in(k for k in REVIEW_KINDS if k != 'hidden')}) GROUP BY ai_article_kind",
            params,
        ):
            kind_counts[k] = n
        kind_counts["hidden"] = sum(kind_counts[k] for k in HIDDEN_ARTICLE_KINDS)
        if kind == "hidden":
            where += f" AND ai_article_kind IN ({_HIDDEN_KINDS_SQL})"
        elif kind:
            where += " AND ai_article_kind = ?"
            params.append(kind)
        counts = {s: 0 for s in STATUSES}
        for st, n in conn.execute(f"SELECT status, COUNT(*) FROM papers WHERE {where} GROUP BY status", params):
            counts[st] = n
        total = sum(counts.values()) if status == "all" else counts[status]
        row_where, row_params = where, list(params)
        if status != "all":
            row_where += " AND status = ?"
            row_params.append(status)
        got = conn.execute(
            f"SELECT * FROM papers WHERE {row_where} ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
            row_params + [limit, offset],
        ).fetchall()
        months = [
            r[0] for r in conn.execute(
                f"SELECT DISTINCT bucket_month FROM papers WHERE {base} ORDER BY bucket_month DESC"
            )
        ]
    return {
        "rows": [_review_row(r) for r in got], "total": total, "counts": counts, "kind_counts": kind_counts,
        "months": months,
    }


def set_human_status(conn: sqlite3.Connection, items: list, actor: str) -> dict:
    """사람(관리자)의 판정 배치 적용(배치 1개 = 트랜잭션 1개). 항목 {ref, status}, status는 HUMAN_STATUSES 중 하나.

    항목별 결과: updated / unchanged / gone(모르는 ref·tombstone) / invalid. updated·unchanged에는 current(검토 목록 행)가 붙는다.
    - 적용하면 status_source는 **항상** 'human'이 된다. status가 그대로여도 마찬가지다(사람이 확인했다는 표시).
    - status가 바뀌거나 출처만 바뀌면 change_seq와 status_seq를 새 seq로 올리고 status_history에 actor와 함께 남긴다.
      출처만 바뀌는 경우에도 change_seq를 올리는 이유: 변경 피드가 paper_autodown의 유일한 수신 경로이고, 충돌 규칙
      (human AND status_seq > seen_through)이 이 확인을 오래된 자동 판정으로부터 지키려면 status_seq가 새로워야 한다.
    - 이미 human이고 status도 같으면 unchanged(아무것도 쓰지 않는다).
    """
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor")
    results = []
    with write_tx(conn):
        _ensure_history_actor(conn)
        _ensure_article_kind_columns(conn)  # current(검토 목록 행)에 글 종류 열이 들어간다
        now = _now()
        for item in items:
            results.append(_set_one_human_status(conn, item, actor, now))
    return {"results": results}


def _set_one_human_status(conn: sqlite3.Connection, item, actor: str, now: str) -> dict:
    ref = item.get("ref") if isinstance(item, dict) else None
    if not isinstance(ref, str) or not ref:
        return {"ref": None, "result": "invalid"}
    status = item.get("status")
    if status not in HUMAN_STATUSES:
        return {"ref": ref, "result": "invalid"}
    row = conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()
    if row is None or row["deleted_at"] is not None:
        return {"ref": ref, "result": "gone"}
    changed = _update_paper(
        conn, row, {"status": status}, now=now, status_source="human", actor=actor, claim_source=True
    )
    current = _review_row(conn.execute("SELECT * FROM papers WHERE id=?", (row["id"],)).fetchone())
    return {"ref": ref, "result": "updated" if changed else "unchanged", "current": current}


def set_human_article_kind(conn: sqlite3.Connection, items: list, actor: str) -> dict:
    """관리자의 글 종류 지정 배치(배치 1개 = 트랜잭션 1개). 항목 {ref, kind}, kind는 HUMAN_ARTICLE_KINDS 중 하나:
    'research'(AI 가 숨긴 행 복원) / 'non_research'(관리자가 직접 숨김).

    항목별 결과: updated / unchanged / gone(모르는 ref·tombstone) / invalid. updated·unchanged에는 current(검토 목록 행)가 붙는다.
    - 적용하면 article_kind_source는 항상 'human'이 되어, 이후 paper_autodown의 AI 표시가 덮어쓰지 않는다.
    - 값이나 출처가 바뀌면 updated, 이미 같은 값의 human 지정이면 unchanged(아무것도 쓰지 않는다).
    - 판정(status)·change_seq는 건드리지 않는다(내부 표시이므로 변경 피드로 나가지 않는다).
      숨김 여부가 바뀌면 그 월 CSV를 다음 export_dirty에서 다시 쓰도록 표시한다.
    """
    if not isinstance(actor, str) or not actor.strip():
        raise ValueError("actor")
    results = []
    with write_tx(conn):
        _ensure_article_kind_columns(conn)
        for item in items:
            results.append(_set_one_human_article_kind(conn, item))
    return {"results": results}


def _set_one_human_article_kind(conn: sqlite3.Connection, item) -> dict:
    ref = item.get("ref") if isinstance(item, dict) else None
    if not isinstance(ref, str) or not ref:
        return {"ref": None, "result": "invalid"}
    kind = item.get("kind")
    if kind not in HUMAN_ARTICLE_KINDS:
        return {"ref": ref, "result": "invalid"}
    row = conn.execute("SELECT * FROM papers WHERE ref=?", (ref,)).fetchone()
    if row is None or row["deleted_at"] is not None:
        return {"ref": ref, "result": "gone"}
    changed = row["ai_article_kind"] != kind or row["article_kind_source"] != "human"
    if changed:
        conn.execute("UPDATE papers SET ai_article_kind=?, article_kind_source='human' WHERE id=?", (kind, row["id"]))
        if _is_hidden_kind(row["ai_article_kind"]) != _is_hidden_kind(kind):
            _mark_csv_dirty(conn, row["bucket_month"])
    current = _review_row(conn.execute("SELECT * FROM papers WHERE id=?", (row["id"],)).fetchone())
    return {"ref": ref, "result": "updated" if changed else "unchanged", "current": current}


# ---------- CSV export (_save_cache 바이트 호환) ----------

def csv_path(month: str, cache_dir: str | None = None) -> str:
    return os.path.join(cache_dir or CACHE_DIR, f"papers_{month}.csv")


def _check_month(month: str) -> str:
    if not isinstance(month, str) or not _MONTH_RE.match(month):
        raise ValueError(f"월 형식이 잘못되었습니다(YYYY-MM): {month!r}")
    return month


def slim_csv_path(month: str, cache_dir: str | None = None) -> str:
    """공개 슬림 CSV 경로. 이름이 `papers_` 로 시작하지 않는 것은 의도적이다
    (migrate_from_notion 게이트가 `papers_*.csv` 를 월 CSV 로 간주해 훑기 때문)."""
    return os.path.join(cache_dir or CACHE_DIR, f"slim_papers_{month}.csv")


_DOI_RE = re.compile(r"""10\.\d{4,9}/[^\s?&#"'<>]+""")


def doi_from_url(url: str | None) -> str:
    """저장된 url 에서 DOI 를 결정적으로 뽑는다: URL 디코딩 2회 -> `10.NNNN/...` 첫 매치 -> 끝의 `.`/`)` 제거.
    추적 리다이렉트 링크 등에 DOI 가 없으면 빈 문자열."""
    decoded = urllib.parse.unquote(urllib.parse.unquote(url or ""))
    m = _DOI_RE.search(decoded)
    return m.group(0).rstrip(".)") if m else ""


def _csv_rows(conn: sqlite3.Connection, month: str) -> tuple[list[list[str]], list[list[str]], int, int, int]:
    """(전체 CSV 행, 슬림 CSV 행, 읽은 시점 max change_seq, 제외 저널로 드롭한 건수, AI 비논문·논평으로 숨긴 건수). 한 읽기 스냅샷.

    정렬은 발행일 오름차순(없는 날짜는 끝), 같으면 id. tombstone은 제외, 이관 중복 행은 포함한다
    (기존 CSV에도 들어 있던 행들이므로). 슬림 행은 전체 행과 같은 행·순서에 (id, doi)를 붙이고 url·status 를 뺀 것.
    ai_article_kind 가 HIDDEN_ARTICLE_KINDS(non_research·comment)인 행은 공개 CSV(전체·슬림 모두)에서 뺀다 - DB 에는 남는다.
    글 종류 열이 아직 없는 DB(읽기 전용 연결로 지연 생성되는 슬림 포함)는 숨길 행이 없는 것으로 본다.
    """
    with read_tx(conn):
        seq = conn.execute(
            "SELECT COALESCE(MAX(change_seq), 0) FROM papers WHERE bucket_month=?", (month,)
        ).fetchone()[0]
        kind_col = "ai_article_kind" if not _missing_article_kind_columns(conn) else "NULL AS ai_article_kind"
        got = conn.execute(
            f"SELECT id, title, journal, pub_date, url, status, gpt_reason, {kind_col} FROM papers "
            "WHERE bucket_month=? AND deleted_at IS NULL ORDER BY pub_date IS NULL, pub_date, id",
            (month,),
        ).fetchall()
    rows, slim_rows, dropped, hidden = [], [], 0, 0
    for r in got:
        if is_excluded_journal(r["journal"]):
            dropped += 1
            continue
        if _is_hidden_kind(r["ai_article_kind"]):
            hidden += 1
            continue
        rows.append([r["title"], r["journal"], r["pub_date"] or "", r["url"], r["status"], r["gpt_reason"]])
        slim_rows.append(
            [str(r["id"]), r["title"], r["journal"], r["pub_date"] or "", doi_from_url(r["url"]), r["gpt_reason"]]
        )
    return rows, slim_rows, seq, dropped, hidden


def _write_rows_atomic(path: str, columns: list[str], rows: list[list[str]], lineterminator: str) -> None:
    """임시 파일에 쓰고 os.replace로 교체(서버의 mtime 메모가 그대로 동작). QUOTE_ALL, utf-8-sig(BOM)."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.{threading.get_ident()}.tmp"  # 대시보드 스레드끼리 같은 월을 동시에 export 해도 충돌하지 않게
    try:
        with open(tmp, "w", encoding="utf-8-sig", newline="") as f:
            writer = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator=lineterminator)
            writer.writerow(columns)
            writer.writerows(rows)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.remove(tmp)
        raise


def _write_csv_atomic(path: str, rows: list[list[str]]) -> None:
    """전체 CSV 를 쓴다. 형식은 _save_cache와 같다: QUOTE_ALL, utf-8-sig, 헤더 6열, 줄바꿈은 pandas 기본(os.linesep)."""
    _write_rows_atomic(path, CSV_COLUMNS, rows, os.linesep)


def _write_slim_csv_atomic(path: str, rows: list[list[str]]) -> None:
    """슬림 CSV 를 쓴다. 서빙 본문이 곧 이 파일의 바이트이고 버전 해시의 입력이므로 줄바꿈을 OS 와 무관하게 `\\n` 으로 고정한다."""
    _write_rows_atomic(path, SLIM_COLUMNS, rows, "\n")


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

    같은 스냅샷에서 공개 슬림 CSV(cache/slim_papers_<month>.csv)도 함께 쓴다(전체 CSV 다음에 쓰므로 슬림의 mtime 이
    전체보다 작지 않다 - ensure_slim_csv 의 신선도 판정 근거).
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
            rows, slim_rows, seq, dropped, hidden = _csv_rows(conn, month)
            if dropped:
                logger.info("제외 저널 %d건 CSV 캐시에서 드롭 (%s)", dropped, month)
            if hidden:
                logger.info("AI 비논문·논평 %d건 CSV 캐시에서 숨김 (%s)", hidden, month)
            path = csv_path(month, cache_dir)
            _write_csv_atomic(path, rows)
            _write_slim_csv_atomic(slim_csv_path(month, cache_dir), slim_rows)
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
    """max(change_seq) > csv_seq:<month> 인 bucket(또는 CSV·슬림 CSV 파일이 없는 bucket)만 export. {월: 행 수}.
    바뀐 게 없으면 아무것도 하지 않는다(cron 15분 간격 전제). 슬림 파일이 없는 배포 직후의 월도 여기서 채워진다."""
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
            if (
                maxes[month] > seen.get(month, 0)
                or not os.path.exists(csv_path(month, cache_dir))
                or not os.path.exists(slim_csv_path(month, cache_dir))
            ):
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


# ---------- 공개 슬림 CSV: 지연 생성 / 버전 ----------

def _slim_is_fresh(month: str, cache_dir: str | None) -> bool:
    """슬림 파일이 있고, 전체 CSV 가 있다면 그보다 오래되지 않았다(export 는 전체 -> 슬림 순으로 쓴다)."""
    try:
        slim_mtime = os.stat(slim_csv_path(month, cache_dir)).st_mtime_ns
    except OSError:
        return False
    try:
        return slim_mtime >= os.stat(csv_path(month, cache_dir)).st_mtime_ns
    except OSError:
        return True  # 전체 CSV 가 없으면 슬림이 단독으로 선다


def ensure_slim_csv(month: str, conn: sqlite3.Connection | None = None, cache_dir: str | None = None) -> bool:
    """슬림 CSV 가 서빙 가능하도록 보장한다. 없거나 전체 CSV 보다 오래됐으면(배포 직후·수동으로 전체 CSV 를 갈아 끼운 뒤)
    DB 에서 슬림만 다시 쓴다(`.export.lock` 안에서 재확인하므로 동시 요청·진행 중인 export 와 겹쳐도 한 번만 쓴다).
    meta(csv_seq)는 건드리지 않는다. DB 를 못 열면 낡았더라도 기존 슬림 파일이 있는 한 True. 서빙할 슬림이 없으면 False."""
    _check_month(month)
    if _slim_is_fresh(month, cache_dir):
        return True
    path = slim_csv_path(month, cache_dir)
    own = conn is None
    if own:
        try:
            conn = connect(readonly=True)
        except (FileNotFoundError, sqlite3.Error):
            return os.path.exists(path)
    try:
        with _export_lock(cache_dir):
            if _slim_is_fresh(month, cache_dir):
                return True
            _write_slim_csv_atomic(path, _csv_rows(conn, month)[1])
        return True
    except (sqlite3.Error, OSError):
        logger.exception("슬림 CSV 지연 생성 실패: %s", month)
        return os.path.exists(path)
    finally:
        if own:
            conn.close()


_slim_version_memo: dict[str, tuple[tuple[int, int, int], str]] = {}


def slim_version_of(data: bytes) -> str:
    """슬림 CSV 바이트 → 버전. publish_pages.py 가 읽은 바이트로 직접 계산해, 파일명과 내용이 항상 맞게 한다."""
    return hashlib.sha256(data).hexdigest()[:12]


def slim_version(month: str, cache_dir: str | None = None) -> str | None:
    """슬림 CSV 의 버전 = 파일 내용 sha256 앞 12자리(불투명 문자열). 내용이 바뀔 때만 바뀐다.

    csv_seq 만으로는 `refresh_csv.py --all`(제외 저널 목록 변경 등)처럼 seq 는 그대로인데 내용이 바뀌는 재export 를 못 잡고,
    변경 불가(immutable)로 캐시된 옛 URL 이 1년 남는다. 해시는 (mtime_ns, size, inode)가 같으면 재사용한다.
    파일이 없으면 None."""
    path = slim_csv_path(month, cache_dir)
    try:
        st = os.stat(path)
        key = (st.st_mtime_ns, st.st_size, st.st_ino)
        hit = _slim_version_memo.get(path)
        if hit is not None and hit[0] == key:
            return hit[1]
        with open(path, "rb") as f:
            ver = slim_version_of(f.read())
    except OSError:
        return None
    _slim_version_memo[path] = (key, ver)
    return ver


_CSV_FILE_RE = re.compile(r"^papers_(\d{4}-\d{2})\.csv$")


def csv_versions(cache_dir: str | None = None) -> dict[str, str]:
    """{월: 슬림 CSV 버전}, 최신 월 먼저. FIRST_BUCKET_MONTH ~ 이번 달 중 전체 CSV 가 있고(= /api/csv/<월> 이 서빙되는 달)
    슬림 CSV 도 서빙 가능한 달만 담는다. 슬림이 없는 달은 이 호출에서 지연 생성한다."""
    directory = cache_dir or CACHE_DIR
    this_month = _today().strftime("%Y-%m")
    try:
        names = os.listdir(directory)
    except OSError:
        return {}
    months = sorted(
        (m.group(1) for m in map(_CSV_FILE_RE.match, names) if m and FIRST_BUCKET_MONTH <= m.group(1) <= this_month),
        reverse=True,
    )
    out: dict[str, str] = {}
    for month in months:
        if not _MONTH_RE.match(month) or not ensure_slim_csv(month, cache_dir=cache_dir):
            continue
        ver = slim_version(month, cache_dir)
        if ver is not None:
            out[month] = ver
    return out


# ---------- Cloudflare Pages 사본 (publish_pages.py) ----------

# 슬림 CSV 를 *.pages.dev 에서도 내려받게 하는 사본. publish_pages.py 가 배포에 성공한 뒤 이 상태 파일을 쓰고,
# /api/versions 가 읽어 "pages" 로 알린다. 주소는 https://<프로젝트>.pages.dev 모양만 허용한다(끝의 \Z: `$` 는 뒤따르는 개행을 통과시킨다).
PAGES_BASE_RE = re.compile(r"^https://[a-z0-9-]+\.pages\.dev\Z")
PAGES_STATE_NAME = "pages_published.json"


def read_pages_state(cache_dir: str | None = None) -> dict | None:
    """마지막으로 배포에 성공한 Pages 사본 {"base": 주소, "months": {월: 버전}[, "front": 공개 화면 해시]}. 파일이 없거나 깨졌거나
    주소가 규칙에 안 맞으면 None(= 사본 없음으로 취급). months 에서는 월·버전이 문자열인 항목만 남긴다.
    "front" 는 공개 화면(index.html 등)까지 올라간 배포에만 있다 - 옛 형식 상태 파일에는 키 자체가 없다."""
    path = os.path.join(cache_dir or CACHE_DIR, PAGES_STATE_NAME)
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("months"), dict):
        return None
    base = data.get("base")
    if not isinstance(base, str) or not PAGES_BASE_RE.match(base):
        return None
    months = {m: v for m, v in data["months"].items() if isinstance(m, str) and _MONTH_RE.match(m) and isinstance(v, str) and v}
    out = {"base": base, "months": months}
    front = data.get("front")
    if isinstance(front, str) and front:
        out["front"] = front
    return out


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
