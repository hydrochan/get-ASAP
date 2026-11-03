"""dashboard/sync_api.py (+ server.py 위임·/api/refresh_all·/api/csv) 회귀 테스트 - 실제 ThreadingHTTPServer 위에서 검증한다.

합성 토큰·합성 데이터만 쓰며 실제 .env, data/asap.db, cache/ 는 건드리지 않는다.
"""
import gzip
import http.client
import http.server
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

import store
from models import PaperMetadata

TOKEN = "synthetic-test-token-0123456789"


def paper(title, date="2026-09-07"):
    return PaperMetadata(title=title, journal="Synthetic Journal", date=date, url="https://example.invalid/p")


@pytest.fixture
def api(db_file, monkeypatch, tmp_path):
    import config
    import server

    monkeypatch.setenv("ASAP_SYNC_TOKEN", TOKEN)
    monkeypatch.setattr(server, "CACHE_DIR", str(tmp_path / "cache"))  # /api/csv 도 격리
    server.login_attempts.clear()
    server.sessions.clear()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def call(path, body=None, *, token=TOKEN, method="POST", headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        hdrs = dict(headers or {})
        if token is not None:
            hdrs.setdefault("Authorization", f"Bearer {token}")
        payload = raw if raw is not None else (json.dumps(body if body is not None else {}).encode() if method == "POST" else None)
        if payload is not None:
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, path, body=payload, headers=hdrs)
        resp = conn.getresponse()
        data = resp.read()
        out = SimpleNamespace(status=resp.status, headers={k.lower(): v for k, v in resp.getheaders()}, data=data)
        conn.close()
        return out

    def jcall(path, body=None, **kw):
        r = call(path, body, **kw)
        r.json = json.loads(r.data) if r.data else None
        return r

    yield SimpleNamespace(call=call, jcall=jcall, server=server, config=config, port=port, db_file=db_file)
    httpd.shutdown()
    httpd.server_close()
    server.login_attempts.clear()
    server.sessions.clear()


def seed(db_file, *titles):
    c = store.connect(str(db_file))
    try:
        store.save_papers([paper(t) for t in titles], c)
    finally:
        c.close()


# ---------- 인증 / 공통 규칙 ----------

def test_ping_ok_with_no_store_header(api):
    seed(api.db_file, "P1", "P2")
    r = api.jcall("/api/sync/v1/ping")
    assert r.status == 200
    assert {k: v for k, v in r.json.items() if k != "db_epoch"} == {"ok": True, "last_seq": 2, "schema_version": 1}
    assert len(r.json["db_epoch"]) == 36
    assert api.jcall("/api/sync/v1/changes").json["db_epoch"] == r.json["db_epoch"]  # 두 응답이 같은 식별자
    assert r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("application/json")


@pytest.mark.parametrize("header", [None, "Bearer wrong-token", "Bearer ", f"Basic {TOKEN}", TOKEN, f"Bearer {TOKEN}x", f"Bearer {TOKEN[:-1]}"])
def test_missing_or_wrong_token_is_401(api, header):
    headers = {"Authorization": header} if header is not None else {}
    r = api.jcall("/api/sync/v1/ping", token=None, headers=headers)
    assert r.status == 401 and r.json == {"error": "unauthorized"}
    assert r.headers["cache-control"] == "no-store"
    assert TOKEN.encode() not in r.data


def test_unset_token_is_503_for_every_request(api, monkeypatch):
    monkeypatch.delenv("ASAP_SYNC_TOKEN")
    for path in ("/api/sync/v1/ping", "/api/sync/v1/changes", "/api/sync/v1/backup/latest", "/api/sync/v1/nope"):
        r = api.jcall(path, headers={"Authorization": f"Bearer {TOKEN}"}, token=None)
        assert r.status == 503 and r.json == {"error": "sync_disabled"}
    assert api.jcall("/api/sync/v1/ping", method="GET", token=None).status == 503  # GET 도 405 가 아니라 503
    monkeypatch.setenv("ASAP_SYNC_TOKEN", "   ")  # 공백만 있어도 미설정
    assert api.jcall("/api/sync/v1/ping").status == 503


def test_only_post_is_accepted(api):
    for method in ("GET",):
        r = api.jcall("/api/sync/v1/ping", method=method)
        assert r.status == 405 and r.headers["allow"] == "POST" and r.headers["cache-control"] == "no-store"
    assert api.call("/api/sync/v1/ping", method="PUT", raw=b"{}").status in (405, 501)


def test_db_missing_is_503_and_not_created(api, monkeypatch, tmp_path):
    ghost = tmp_path / "ghost" / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(ghost))
    for path in ("/api/sync/v1/ping", "/api/sync/v1/changes", "/api/sync/v1/verdicts", "/api/sync/v1/backup/latest"):
        r = api.jcall(path, {"items": []})
        assert r.status == 503 and r.json == {"error": "db_unavailable"}, path
    assert not ghost.exists() and not ghost.parent.exists()


def test_corrupt_db_is_503_not_500(api):
    api.db_file.write_bytes(b"definitely not a sqlite file" * 100)
    r = api.jcall("/api/sync/v1/ping")
    assert r.status == 503 and r.json == {"error": "db_unavailable"}


def test_body_cap_is_checked_before_reading(api):
    # Content-Length만 2 MiB 초과로 선언하고 본문은 보내지 않는다: 서버가 읽으려 들면 응답이 오지 않고 타임아웃 난다
    conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=5)
    conn.putrequest("POST", "/api/sync/v1/ping")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Length", str(2 * 1024 * 1024 + 1))
    conn.endheaders()
    resp = conn.getresponse()
    assert resp.status == 413 and json.loads(resp.read()) == {"error": "payload_too_large"}
    assert resp.getheader("Cache-Control") == "no-store"
    conn.close()


def test_body_of_exactly_2mib_is_accepted(api):
    body = b'{"after": 0}' + b" " * (2 * 1024 * 1024 - len(b'{"after": 0}'))
    assert len(body) == 2 * 1024 * 1024
    assert api.jcall("/api/sync/v1/changes", raw=body).status == 200


def test_oversized_body_without_valid_token_is_401_not_read(api):
    conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=5)
    conn.putrequest("POST", "/api/sync/v1/ping")
    conn.putheader("Content-Length", str(10 * 1024 * 1024))
    conn.endheaders()
    assert conn.getresponse().status == 401
    conn.close()


def test_discard_unread_body_only_when_unread_and_within_cap(api):
    # 인증 실패(401)·404 는 본문을 읽기 전에 결정된다. 안 읽은 채 닫으면 TCP RST 로 클라이언트가 401 대신 연결 오류를 본다
    # (e2e: Windows 에서 토큰이 틀린 900 KB verdicts 가 ReadError 로 3회 재시도됨). 응답 전에 상한 이내 본문을 버린다.
    import io

    import sync_api

    class Sock:
        def gettimeout(self):
            return None

        def settimeout(self, value):
            pass

    def fake(length, declared=None, read=False):
        return SimpleNamespace(headers={"Content-Length": str(declared if declared is not None else length)},
                               rfile=io.BytesIO(b"x" * length + b"NEXT"), connection=Sock(), _sync_body_read=read)

    h = fake(100_000)
    sync_api._discard_unread_body(h)
    assert h.rfile.read() == b"NEXT"
    h = fake(100, read=True)                       # _read_json_body 가 이미 읽었다 -> 다시 읽으면 블록된다
    sync_api._discard_unread_body(h)
    assert len(h.rfile.read()) == 104
    h = fake(10, declared=sync_api.MAX_BODY_BYTES + 1)   # 상한 초과 선언은 설계대로 읽지 않는다
    sync_api._discard_unread_body(h)
    assert len(h.rfile.read()) == 14
    h = fake(10, declared="abc")
    sync_api._discard_unread_body(h)
    assert len(h.rfile.read()) == 14


def test_big_body_with_wrong_token_is_401_end_to_end(api):
    # 스모크(루프백에서는 타이밍에 따라 수정 없이도 통과할 수 있다): 큰 본문 + 틀린 토큰도 401/404 JSON 을 받는다
    body = json.dumps({"seen_through": 0, "items": [{"ref": "x", "gpt_reason": "g" * (1900 * 1024)}]}).encode()
    r = api.jcall("/api/sync/v1/verdicts", raw=body, token="wrong-token")
    assert r.status == 401 and r.json == {"error": "unauthorized"}
    assert api.jcall("/api/sync/v1/nope", raw=body).status == 404


@pytest.mark.parametrize("raw", [b"not json", b"[1,2]", b'"str"', b"\xff\xfe"])
def test_malformed_json_is_400(api, raw):
    r = api.jcall("/api/sync/v1/changes", raw=raw)
    assert r.status == 400 and r.json == {"error": "bad_request"}


def test_bad_content_length_is_400(api):
    conn = http.client.HTTPConnection("127.0.0.1", api.port, timeout=5)
    conn.putrequest("POST", "/api/sync/v1/ping")
    conn.putheader("Authorization", f"Bearer {TOKEN}")
    conn.putheader("Content-Length", "abc")
    conn.endheaders()
    assert conn.getresponse().status == 400
    conn.close()


def test_empty_body_is_treated_as_empty_object(api):
    assert api.jcall("/api/sync/v1/ping", raw=b"").status == 200


def test_unknown_endpoint_and_version_are_404(api):
    assert api.jcall("/api/sync/v1/nope").status == 404
    assert api.jcall("/api/sync/v2/ping").status == 404
    assert api.jcall("/api/sync/").status == 404


def test_ip_lockout_after_repeated_failures_uses_separate_counter(api, monkeypatch):
    monkeypatch.setattr(api.server, "MAX_ATTEMPTS", 3)
    for _ in range(3):
        assert api.jcall("/api/sync/v1/ping", token="bad").status == 401
    r = api.jcall("/api/sync/v1/ping", token="bad")  # 틀린 토큰은 잠금 중 429
    assert r.status == 429 and r.json == {"error": "locked"}
    assert api.server._check_lockout("sync:127.0.0.1") and not api.server._check_lockout("127.0.0.1")  # 로그인 카운터와 분리
    assert api.jcall("/api/sync/v1/ping").status == 200  # 올바른 토큰은 잠금과 무관하게 통과
    api.server.login_attempts["sync:127.0.0.1"]["locked_until"] = time.time() - 1
    assert api.jcall("/api/sync/v1/ping", token="bad").status == 401  # 만료되면 풀린다


def test_valid_token_is_never_locked_out_by_failures_from_a_shared_ip(api, monkeypatch):
    """Cloudflare 엣지 IP 는 여럿이 공유한다 - 남의 실패(잠금)가 유효 토큰 요청을 막으면 안 되고,
    유효 요청이 남의 실패 카운터를 지워서도 안 된다."""
    monkeypatch.setattr(api.server, "MAX_ATTEMPTS", 3)
    for _ in range(5):
        api.jcall("/api/sync/v1/ping", token="bad")
    assert api.server._check_lockout("sync:127.0.0.1")
    for _ in range(3):
        assert api.jcall("/api/sync/v1/ping").status == 200
    assert api.server._check_lockout("sync:127.0.0.1")  # 성공이 잠금·카운터를 풀지 않는다
    api.server.login_attempts.clear()
    api.jcall("/api/sync/v1/ping", token="bad")
    api.jcall("/api/sync/v1/ping")
    assert api.server.login_attempts["sync:127.0.0.1"]["count"] == 1


# ---------- /changes ----------

def test_changes_validation(api):
    for body in ({"limit": 0}, {"limit": 1001}, {"limit": "5"}, {"limit": True}, {"limit": 1.5}, {"after": -1}, {"after": "0"}):
        r = api.jcall("/api/sync/v1/changes", body)
        assert r.status == 400 and r.json == {"error": "bad_request"}, body
    assert api.jcall("/api/sync/v1/changes", {"limit": 1000}).status == 200
    assert api.jcall("/api/sync/v1/changes", {"limit": 1}).status == 200


def test_changes_default_limit_500_and_pagination(api):
    c = store.connect(str(api.db_file))
    store.save_papers([paper(f"Bulk {i}") for i in range(510)], c)
    c.close()
    first = api.jcall("/api/sync/v1/changes", {}).json
    assert len(first["rows"]) == 500 and first["has_more"] is True and first["next_after"] == 500
    second = api.jcall("/api/sync/v1/changes", {"after": first["next_after"]}).json
    assert len(second["rows"]) == 10 and second["has_more"] is False and second["next_after"] == 510
    assert second["rows"][0]["seq"] == 501 and second["last_seq"] == 510
    assert set(second["rows"][0]) == {
        "ref", "title", "journal", "pub_date", "url", "status", "gpt_reason", "status_source", "status_seq",
        "created_at", "bucket_month", "deleted", "dup_of_ref", "seq",
    }


# ---------- /verdicts ----------

def test_verdicts_roundtrip_conflict_gone_and_idempotency(api):
    seed(api.db_file, "Verdict me", "Human held")
    feed = {r["title"]: r for r in api.jcall("/api/sync/v1/changes").json["rows"]}
    ref, human_ref = feed["Verdict me"]["ref"], feed["Human held"]["ref"]
    c = store.connect(str(api.db_file))
    c.execute("UPDATE papers SET status='관련', status_source='human', status_seq=50 WHERE ref=?", (human_ref,))
    c.close()
    body = {"seen_through": 2, "items": [
        {"ref": ref, "status": "관련", "gpt_reason": "it is relevant"},
        {"ref": human_ref, "status": "무관", "gpt_reason": "no"},
        {"ref": "does-not-exist", "status": "무관"},
        {"ref": ref, "status": "bogus"},
    ]}
    r = api.jcall("/api/sync/v1/verdicts", body)
    assert r.status == 200
    assert [x["result"] for x in r.json["results"]] == ["updated", "conflict", "gone", "invalid"]
    assert r.json["results"][1]["current"]["status_source"] == "human" and r.json["results"][1]["current"]["status"] == "관련"
    last = r.json["last_seq"]
    again = api.jcall("/api/sync/v1/verdicts", body).json  # 같은 요청을 재시도해도 결과가 같고 아무것도 더 바뀌지 않는다
    assert [x["result"] for x in again["results"]] == ["unchanged", "conflict", "gone", "invalid"]
    assert again["last_seq"] == last
    changed = {x["ref"]: x for x in api.jcall("/api/sync/v1/changes").json["rows"]}[ref]
    assert (changed["status"], changed["status_source"], changed["gpt_reason"]) == ("관련", "paper_autodown", "it is relevant")


def test_verdicts_validation(api):
    big = {"items": [{"ref": f"r{i}", "status": "관련"} for i in range(201)]}
    assert api.jcall("/api/sync/v1/verdicts", big).status == 400
    ok = {"items": [{"ref": f"r{i}", "status": "관련"} for i in range(200)]}
    r = api.jcall("/api/sync/v1/verdicts", ok)
    assert r.status == 200 and len(r.json["results"]) == 200
    for body in ({}, {"items": "x"}, {"items": {}}, {"items": [], "seen_through": -1}, {"items": [], "seen_through": "1"}):
        assert api.jcall("/api/sync/v1/verdicts", body).status == 400, body


# ---------- /verdicts: article_kind (AI 비논문·논평 표시, 공개 화면에서만 숨김) ----------

def kind_state(db_file, ref):
    c = store.connect(str(db_file))
    try:
        r = c.execute(
            "SELECT ai_article_kind, article_kind_source, change_seq, status, status_source FROM papers WHERE ref=?", (ref,)
        ).fetchone()
        return tuple(r), store.last_seq(c)
    finally:
        c.close()


def test_verdict_kind_only_item_is_updated_then_unchanged_without_touching_seq_or_feed(api):
    seed(api.db_file, "Kind only")
    feed = api.jcall("/api/sync/v1/changes").json
    ref = feed["rows"][0]["ref"]
    before, last = kind_state(api.db_file, ref)
    r = api.jcall("/api/sync/v1/verdicts", {"seen_through": 0, "items": [{"ref": ref, "article_kind": "non_research"}]})
    assert r.status == 200 and [x["result"] for x in r.json["results"]] == ["updated"]  # status/gpt_reason 이 모두 없어도 받는다
    after, last2 = kind_state(api.db_file, ref)
    assert after[:2] == ("non_research", "ai")
    assert after[2:] == before[2:] and last2 == last == r.json["last_seq"]  # change_seq·status·last_seq 불변
    again = api.jcall("/api/sync/v1/verdicts", {"seen_through": 0, "items": [{"ref": ref, "article_kind": "non_research"}]})
    assert [x["result"] for x in again.json["results"]] == ["unchanged"]
    # 피드에는 아무것도 새로 나가지 않고, 피드 행에 글 종류 열도 없다
    after_feed = api.jcall("/api/sync/v1/changes", {"after": last}).json
    assert after_feed["rows"] == [] and all("article_kind" not in k and "ai_article_kind" not in k for k in feed["rows"][0])
    # 값이 바뀌면 다시 updated
    r3 = api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "article_kind": "research"}]})
    assert [x["result"] for x in r3.json["results"]] == ["updated"] and kind_state(api.db_file, ref)[0][0] == "research"


def test_verdict_kind_missing_or_null_is_untouched_and_invalid_kind_rejects_whole_item(api):
    seed(api.db_file, "Kind keep")
    ref = api.jcall("/api/sync/v1/changes").json["rows"][0]["ref"]
    api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "article_kind": "comment"}]})
    r = api.jcall("/api/sync/v1/verdicts", {"items": [
        {"ref": ref, "status": "관련"},                                    # 키 없음 -> 그대로
        {"ref": ref, "status": "관련", "gpt_reason": "r", "article_kind": None},   # null -> 그대로
        {"ref": ref, "article_kind": None},
    ]})
    assert [x["result"] for x in r.json["results"]] == ["updated", "updated", "unchanged"]
    assert kind_state(api.db_file, ref)[0][:2] == ("comment", "ai")
    bad = [{"ref": ref, "status": "무관", "article_kind": k} for k in ("bogus", "Research", "non-research", "", 5, True, ["research"], {})]
    res = api.jcall("/api/sync/v1/verdicts", {"items": bad})
    assert [x["result"] for x in res.json["results"]] == ["invalid"] * len(bad)
    state = kind_state(api.db_file, ref)[0]
    assert state[:2] == ("comment", "ai") and state[3] == "관련"  # 잘못된 kind 면 같이 온 status 도 적용되지 않는다


def test_verdict_kind_is_recorded_even_when_status_conflicts(api):
    seed(api.db_file, "Human held kind")
    ref = api.jcall("/api/sync/v1/changes").json["rows"][0]["ref"]
    c = store.connect(str(api.db_file))
    c.execute("UPDATE papers SET status='관련', status_source='human', status_seq=50 WHERE ref=?", (ref,))
    c.close()
    r = api.jcall("/api/sync/v1/verdicts", {"seen_through": 1, "items": [
        {"ref": ref, "status": "무관", "gpt_reason": "no", "article_kind": "non_research"}]})
    assert [x["result"] for x in r.json["results"]] == ["conflict"]
    state = kind_state(api.db_file, ref)[0]
    assert state[:2] == ("non_research", "ai")                  # 표시는 판정 충돌과 무관하게 기록된다
    assert state[3:] == ("관련", "human")                       # 판정은 사람 것이 그대로
    # 결과 어휘는 paper_autodown 이 파싱하는 5가지 안에 머문다
    res = api.jcall("/api/sync/v1/verdicts", {"items": [
        {"ref": ref, "article_kind": "review"}, {"ref": ref, "article_kind": "review"}, {"ref": "nope", "article_kind": "review"},
        {"ref": ref, "article_kind": "x"}]})
    assert [x["result"] for x in res.json["results"]] == ["updated", "unchanged", "gone", "invalid"]


def test_verdict_kind_does_not_overwrite_a_human_choice(api):
    seed(api.db_file, "Human kind")
    ref = api.jcall("/api/sync/v1/changes").json["rows"][0]["ref"]
    c = store.connect(str(api.db_file))
    c.execute("UPDATE papers SET ai_article_kind='research', article_kind_source='human' WHERE ref=?", (ref,))
    c.close()
    r = api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "article_kind": "non_research"}]})
    assert [x["result"] for x in r.json["results"]] == ["unchanged"]
    assert kind_state(api.db_file, ref)[0][:2] == ("research", "human")
    # status 와 함께 와도 판정은 적용되고 사람이 정한 kind 는 그대로, 결과는 status 변화만 반영한다
    r = api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "status": "관련", "article_kind": "comment"}]})
    assert [x["result"] for x in r.json["results"]] == ["updated"]
    assert kind_state(api.db_file, ref)[0][:2] == ("research", "human")


def test_verdict_kind_hide_change_marks_month_csv_dirty_but_other_kinds_do_not(api):
    seed(api.db_file, "Dirty one")
    ref = api.jcall("/api/sync/v1/changes").json["rows"][0]["ref"]
    c = store.connect(str(api.db_file))
    store.export_all(c)  # store.CACHE_DIR 는 db_file 픽스처가 tmp 로 격리해 둔다
    assert c.execute("SELECT 1 FROM meta WHERE key='csv_seq:2026-09'").fetchone() is not None
    c.close()
    api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "article_kind": "review"}]})  # 숨김 여부 불변
    c = store.connect(str(api.db_file))
    assert c.execute("SELECT 1 FROM meta WHERE key='csv_seq:2026-09'").fetchone() is not None
    c.close()
    api.jcall("/api/sync/v1/verdicts", {"items": [{"ref": ref, "article_kind": "comment"}]})   # 숨김 -> 메모 삭제
    c = store.connect(str(api.db_file))
    assert c.execute("SELECT 1 FROM meta WHERE key='csv_seq:2026-09'").fetchone() is None
    c.close()


# ---------- /backup/latest ----------

def test_backup_latest(api):
    import backup_db
    import datetime as dt

    assert api.jcall("/api/sync/v1/backup/latest").status == 404
    seed(api.db_file, "Backup content")
    older = backup_db.make_backup(str(api.db_file), now=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))
    newest = backup_db.make_backup(str(api.db_file), now=dt.datetime(2026, 9, 2, tzinfo=dt.timezone.utc))
    r = api.call("/api/sync/v1/backup/latest")
    assert r.status == 200
    assert r.headers["content-type"] == "application/gzip" and r.headers["cache-control"] == "no-store"
    assert r.headers["x-backup-name"] == newest.replace("\\", "/").split("/")[-1] and int(r.headers["content-length"]) == len(r.data)
    raw = gzip.decompress(r.data)
    assert raw.startswith(b"SQLite format 3")
    tmp = api.db_file.parent / "restored_from_api.db"
    tmp.write_bytes(raw)
    check = sqlite3.connect(str(tmp))
    assert check.execute("SELECT title FROM papers").fetchall() == [("Backup content",)]
    check.close()
    assert older != newest


def test_backup_latest_ignores_in_progress_temp_files(api):
    import backup_db
    import datetime as dt

    seed(api.db_file, "x")
    done = backup_db.make_backup(str(api.db_file), now=dt.datetime(2026, 9, 1, tzinfo=dt.timezone.utc))
    (api.db_file.parent / "backups" / "asap-20260930-000000.db.gz.123.tmp").write_bytes(b"partial")
    assert api.call("/api/sync/v1/backup/latest").headers["x-backup-name"] == done.replace("\\", "/").split("/")[-1]


# ---------- 기존 대시보드 API 호환 ----------

def test_refresh_all_exports_from_sqlite_with_same_response_keys(api, tmp_path):
    c = store.connect(str(api.db_file))
    store.save_papers([paper("Aug", date="2026-08-30"), paper("Sep", date="2026-09-05")], c, today=__import__("datetime").date(2026, 9, 20))
    c.close()
    assert api.jcall("/api/refresh_all", {}, token=None).status == 401  # 세션 필요
    api.server.sessions["admintoken"] = {"user": "synthetic-admin", "expires": time.time() + 600}
    api.config.DASHBOARD_ADMINS.add("synthetic-admin")
    try:
        r = api.jcall("/api/refresh_all", {}, token=None, headers={"Cookie": "session=admintoken"})
    finally:
        api.config.DASHBOARD_ADMINS.discard("synthetic-admin")
    assert r.status == 200
    assert set(r.json) == {"ok", "refreshed_months", "message"} and r.json["ok"] is True
    assert r.json["refreshed_months"] == ["2026-08", "2026-09"] and "2개월" in r.json["message"] and "(2건)" in r.json["message"]
    assert (tmp_path / "cache" / "papers_2026-08.csv").exists() and (tmp_path / "cache" / "papers_2026-09.csv").exists()


def test_api_csv_still_serves_exported_cache_without_status_column(api, tmp_path):
    c = store.connect(str(api.db_file))
    store.save_papers([paper("Served paper", date="2026-09-05")], c, today=__import__("datetime").date(2026, 9, 20))
    store.export_all(c)
    c.close()
    r = api.call("/api/csv/2026-09", method="GET", token=None)
    assert r.status == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.data.decode("utf-8-sig")
    assert '"title","journal","date","url","gpt_reason"' in text and "status" not in text.splitlines()[0]
    assert "Served paper" in text
