"""publish_pages.py(슬림 CSV -> Cloudflare Pages) + /api/versions 의 "pages" 필드 회귀 테스트.

  staging: csv/<월>.<버전>.csv (버전 = 읽은 바이트의 sha256 앞 12자), _headers, robots.txt - 매번 새로 만든다
    + 공개 화면: index.html, 그것이 참조하는 정적 파일, versions.json({월: 버전}) / 상태 파일의 front 해시(화면만 바뀌어도 배포)
  지난 배포와 같으면 배포 없이 종료 / 배포 성공 뒤에만 cache/pages_published.json 을 쓴다(실패·시간 초과면 그대로)
  PAGES_BASE_URL 이 https://<project>.pages.dev 가 아니면 거부 / 설정이 없으면 비정상 종료
  /api/versions: 현재 버전과 같은 사본이 있는 달만 "pages" 에 담고, 상태 파일이 없거나 깨졌으면 키를 뺀다

합성 파일(tmp_path)과 가짜 subprocess.run 만 쓴다. 실제 wrangler, 네트워크, .env, cache/ 는 건드리지 않는다.
"""
import datetime as dt
import hashlib
import http.client
import http.server
import json
import os
import re
import subprocess
import threading
from types import SimpleNamespace

import pytest

import publish_pages
import store

BASE = "https://demo-proj.pages.dev"
TOKEN = "test-token-not-real"
ACCOUNT = "test-account-not-real"
SLIM_09 = "﻿id,title,journal,date,doi,gpt_reason\n1,Alpha,J,2026-09-01,,why\n".encode("utf-8")
SLIM_10 = "﻿id,title,journal,date,doi,gpt_reason\n2,Beta,J,2026-10-02,,why\n".encode("utf-8")


INDEX_HTML = (
    '<html><head><link rel="stylesheet" href="/static/tailwind.css?v=abc123">'
    '<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script></head><body>'
    '<a href="https://doi.org/x">x</a><a href="#top">t</a></body></html>'
)


def sha12(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:12]


def write_month(cache, month, data):
    """전체 CSV 를 먼저, 슬림을 나중에 써서 슬림이 낡지 않은 것으로 보이게 한다(DB 없이 csv_versions 가 동작)."""
    (cache / f"papers_{month}.csv").write_bytes(b"\xef\xbb\xbf")
    (cache / f"slim_papers_{month}.csv").write_bytes(data)


@pytest.fixture
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    cache.mkdir()
    monkeypatch.setattr(store, "CACHE_DIR", str(cache))
    monkeypatch.setattr(store, "_today", lambda: dt.date(2026, 11, 15))
    wrangler = tmp_path / "wrangler"
    wrangler.write_text("", encoding="utf-8")
    for k in publish_pages.REQUIRED_KEYS + publish_pages.OPTIONAL_KEYS:
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", TOKEN)
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", ACCOUNT)
    monkeypatch.setenv("PAGES_PROJECT", "demo-proj")
    monkeypatch.setenv("PAGES_BASE_URL", BASE)
    monkeypatch.setenv("WRANGLER_BIN", str(wrangler))
    write_month(cache, "2026-09", SLIM_09)
    write_month(cache, "2026-10", SLIM_10)
    dash = tmp_path / "dashboard"
    (dash / "static").mkdir(parents=True)
    (dash / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (dash / "static" / "tailwind.css").write_text("body{margin:0}", encoding="utf-8")
    monkeypatch.setattr(publish_pages, "DASHBOARD_DIR", str(dash))
    return SimpleNamespace(cache=cache, out=cache / "pages_out", state=cache / "pages_published.json", wrangler=wrangler, dash=dash)


@pytest.fixture
def run(monkeypatch):
    """가짜 subprocess.run: 호출을 기록하고 설정한 결과를 돌려준다. result 로 returncode·출력을, error 로 예외를 바꾼다."""
    calls = []
    cfg = SimpleNamespace(returncode=0, stdout="Deployment complete: https://abc123.demo-proj.pages.dev\n", stderr="", error=None)

    def fake(cmd, **kwargs):
        listing = sorted(os.listdir(cmd[3])) if os.path.isdir(cmd[3]) else None  # 배포 시점의 staging 내용
        calls.append(SimpleNamespace(cmd=cmd, kwargs=kwargs, listing=listing))
        if cfg.error:
            raise cfg.error
        return subprocess.CompletedProcess(cmd, cfg.returncode, stdout=cfg.stdout, stderr=cfg.stderr)

    monkeypatch.setattr(publish_pages.subprocess, "run", fake)
    return SimpleNamespace(calls=calls, cfg=cfg)


# ---------- staging ----------

def test_staging_filename_hash_matches_content(env):
    months = publish_pages.build_staging(str(env.cache), str(env.out))
    assert months == {"2026-10": sha12(SLIM_10), "2026-09": sha12(SLIM_09)}
    assert months == store.csv_versions(str(env.cache))  # 서버가 알리는 버전과 같은 공식
    files = sorted(os.listdir(env.out / "csv"))
    assert files == sorted(f"{m}.{v}.csv" for m, v in months.items())
    for name in files:
        data = (env.out / "csv" / name).read_bytes()
        assert name.split(".")[1] == sha12(data)
    assert (env.out / "csv" / f"2026-09.{sha12(SLIM_09)}.csv").read_bytes() == SLIM_09


def test_staging_version_comes_from_bytes_read_not_from_listing(env, monkeypatch):
    """목록(csv_versions)을 만든 뒤 refresh_csv 가 파일을 다시 써도, 파일명은 실제로 읽은 바이트 기준이다."""
    monkeypatch.setattr(store, "csv_versions", lambda cache_dir=None: {"2026-09": "stale-version"})
    months = publish_pages.build_staging(str(env.cache), str(env.out))
    assert months == {"2026-09": sha12(SLIM_09)}
    assert os.listdir(env.out / "csv") == [f"2026-09.{sha12(SLIM_09)}.csv"]


def test_staging_headers_and_robots(env):
    publish_pages.build_staging(str(env.cache), str(env.out))
    headers = (env.out / "_headers").read_text(encoding="utf-8")
    assert (  # 기존 /csv/* 규칙은 그대로
        "/csv/*\n"
        "  Access-Control-Allow-Origin: *\n"
        "  Content-Type: text/plain; charset=utf-8\n"
        "  Cache-Control: public, max-age=31536000, immutable\n"
    ) in headers
    assert headers.startswith("/*\n")
    assert "  Content-Security-Policy: default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.tailwindcss.com https://cdn.jsdelivr.net; " in headers
    assert "connect-src 'self' https://get-asap.hydrochan.com; frame-ancestors 'none'; base-uri 'self'" in headers
    assert "  X-Content-Type-Options: nosniff\n" in headers
    assert "  Referrer-Policy: strict-origin-when-cross-origin\n" in headers
    assert "  X-Frame-Options: DENY\n" in headers
    assert "/versions.json\n  Cache-Control: no-store\n" in headers
    assert "/index.html\n  Cache-Control: no-cache\n" in headers
    assert (env.out / "robots.txt").read_bytes() == b"User-agent: *\nDisallow: /\n"


def test_staging_is_rebuilt_from_scratch(env):
    publish_pages.build_staging(str(env.cache), str(env.out))
    old = env.out / "csv" / f"2026-09.{sha12(SLIM_09)}.csv"
    assert old.exists()
    new = SLIM_09 + b"3,Gamma,J,2026-09-03,,why\n"
    write_month(env.cache, "2026-09", new)
    months = publish_pages.build_staging(str(env.cache), str(env.out))
    assert not old.exists()  # 옛 버전 파일은 다음 배포에 실리지 않는다
    assert (env.out / "csv" / f"2026-09.{months['2026-09']}.csv").read_bytes() == new


def test_staging_skips_files_over_pages_limit(env, monkeypatch):
    monkeypatch.setattr(publish_pages, "MAX_FILE_BYTES", len(SLIM_09))
    write_month(env.cache, "2026-10", SLIM_10 + b"x" * 10)
    assert list(publish_pages.build_staging(str(env.cache), str(env.out))) == ["2026-09"]


# ---------- 공개 화면 staging ----------

def stage(env):
    months = publish_pages.build_staging(str(env.cache), str(env.out))
    return months, publish_pages.stage_front(str(env.out), months)


def test_front_stages_index_referenced_assets_and_versions_json(env):
    months, front = stage(env)
    assert (env.out / "index.html").read_text(encoding="utf-8") == INDEX_HTML
    assert (env.out / "static" / "tailwind.css").read_text(encoding="utf-8") == "body{margin:0}"  # 쿼리(?v=)는 경로에서 뗀다
    versions = json.loads((env.out / "versions.json").read_text(encoding="utf-8"))
    assert versions == months
    assert list(versions) == ["2026-10", "2026-09"]  # 최신 월 먼저
    assert re.fullmatch(r"[0-9a-f]{12}", front)
    assert not (env.out / "https:").exists()  # 외부 URL·#앵커는 파일로 보지 않는다


def test_front_hash_tracks_html_asset_and_versions(env):
    _, h1 = stage(env)
    assert stage(env)[1] == h1  # 같은 입력이면 같은 해시
    (env.dash / "index.html").write_text(INDEX_HTML + "<!-- 2 -->", encoding="utf-8")
    h2 = stage(env)[1]
    assert h2 != h1
    (env.dash / "static" / "tailwind.css").write_text("body{margin:1px}", encoding="utf-8")
    h3 = stage(env)[1]
    assert h3 not in (h1, h2)
    write_month(env.cache, "2026-10", SLIM_10 + b"7,Eta,J,2026-10-07,,why\n")
    assert stage(env)[1] != h3  # versions.json 이 달라진다


def test_front_not_staged_when_asset_missing_or_html_absent(env):
    (env.dash / "static" / "tailwind.css").unlink()
    _, front = stage(env)
    assert front is None and not (env.out / "versions.json").exists()
    (env.dash / "index.html").unlink()
    assert stage(env)[1] is None


def test_front_ref_outside_dashboard_is_refused(env):
    (env.dash / "index.html").write_text('<script src="/../secret.js"></script>', encoding="utf-8")
    (env.dash.parent / "secret.js").write_text("x", encoding="utf-8")
    assert stage(env)[1] is None


# ---------- 배포 / 상태 ----------

def test_deploy_success_runs_wrangler_and_writes_state(env, run, caplog):
    caplog.set_level("INFO")
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 1
    call = run.calls[0]
    assert call.cmd[0] == str(env.wrangler)
    assert call.cmd[1:3] == ["pages", "deploy"]
    assert call.cmd[3] == str(env.out)
    assert call.cmd[call.cmd.index("--project-name") + 1] == "demo-proj"
    assert call.cmd[call.cmd.index("--branch") + 1] == "main"
    assert "--commit-dirty=true" in call.cmd
    assert not call.kwargs.get("shell")  # 셸 없이 인자 리스트로
    assert call.kwargs["timeout"] == publish_pages.DEPLOY_TIMEOUT_SEC
    assert call.kwargs["env"]["CLOUDFLARE_API_TOKEN"] == TOKEN
    assert call.kwargs["env"]["CLOUDFLARE_ACCOUNT_ID"] == ACCOUNT
    assert TOKEN not in " ".join(call.cmd) and ACCOUNT not in " ".join(call.cmd)  # 인자로는 넘기지 않는다
    assert call.listing == ["_headers", "csv", "index.html", "robots.txt", "static", "versions.json"]
    assert TOKEN not in caplog.text and ACCOUNT not in caplog.text

    state = json.loads(env.state.read_text(encoding="utf-8"))
    assert state["base"] == BASE
    assert state["months"] == {"2026-10": sha12(SLIM_10), "2026-09": sha12(SLIM_09)}
    dt.datetime.fromisoformat(state["published_at"])
    assert re.fullmatch(r"[0-9a-f]{12}", state["front"])
    assert not list(env.cache.glob("*.tmp"))


def test_unchanged_skips_deploy(env, run, caplog):
    caplog.set_level("INFO")
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 1
    before = env.state.read_text(encoding="utf-8")
    caplog.clear()
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 1  # 배포하지 않음
    assert "변경 없음" in caplog.text
    assert env.state.read_text(encoding="utf-8") == before


def test_front_change_alone_triggers_deploy(env, run):
    assert publish_pages.main([]) == 0
    first = json.loads(env.state.read_text(encoding="utf-8"))
    assert publish_pages.main([]) == 0 and len(run.calls) == 1  # 그대로면 생략
    (env.dash / "index.html").write_text(INDEX_HTML + "<!-- 화면만 변경 -->", encoding="utf-8")
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 2
    second = json.loads(env.state.read_text(encoding="utf-8"))
    assert second["months"] == first["months"] and second["front"] != first["front"]


def test_old_format_state_without_front_is_tolerated_and_redeploys(env, run):
    months = store.csv_versions(str(env.cache))
    env.state.write_text(json.dumps({"base": BASE, "months": months, "published_at": "2026-11-01T00:00:00"}), encoding="utf-8")
    assert store.read_pages_state(str(env.cache)) == {"base": BASE, "months": months}  # front 키 없이도 읽힌다
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 1  # 옛 형식 -> 화면을 처음 올리므로 배포
    assert "front" in json.loads(env.state.read_text(encoding="utf-8"))


def test_front_unavailable_deploys_csv_only_and_state_has_no_front(env, run):
    (env.dash / "index.html").unlink()
    assert publish_pages.main([]) == 0
    assert "front" not in json.loads(env.state.read_text(encoding="utf-8"))
    assert publish_pages.main([]) == 0 and len(run.calls) == 1  # CSV 만이어도 변경 없으면 생략


def test_force_deploys_even_if_unchanged(env, run):
    assert publish_pages.main([]) == 0
    assert publish_pages.main(["--force"]) == 0
    assert len(run.calls) == 2


def test_changed_month_redeploys_and_updates_state(env, run):
    assert publish_pages.main([]) == 0
    new = SLIM_10 + b"4,Delta,J,2026-10-04,,why\n"
    write_month(env.cache, "2026-10", new)
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 2
    state = json.loads(env.state.read_text(encoding="utf-8"))
    assert state["months"]["2026-10"] == sha12(new)
    assert state["months"]["2026-09"] == sha12(SLIM_09)


def test_changed_base_url_redeploys(env, run, monkeypatch):
    assert publish_pages.main([]) == 0
    monkeypatch.setenv("PAGES_BASE_URL", "https://other-proj.pages.dev")
    assert publish_pages.main([]) == 0
    assert len(run.calls) == 2
    assert json.loads(env.state.read_text(encoding="utf-8"))["base"] == "https://other-proj.pages.dev"


@pytest.mark.parametrize("how", ["exit_code", "timeout", "oserror"])
def test_failed_deploy_leaves_state_untouched(env, run, caplog, how):
    caplog.set_level("INFO")
    # 처음엔 상태 파일이 아예 없다
    if how == "exit_code":
        run.cfg.returncode = 1
        run.cfg.stderr = f"boom, token was {TOKEN} account {ACCOUNT}"
    elif how == "timeout":
        run.cfg.error = subprocess.TimeoutExpired(cmd="wrangler", timeout=1)
    else:
        run.cfg.error = OSError(f"cannot exec with {TOKEN}")
    assert publish_pages.main([]) == 1
    assert not env.state.exists()
    assert TOKEN not in caplog.text and ACCOUNT not in caplog.text  # 출력에 섞여 나와도 로그에는 가려서 남긴다

    # 성공한 상태가 있을 때 실패해도 그 상태 그대로
    run.cfg.returncode, run.cfg.stderr, run.cfg.error = 0, "", None
    assert publish_pages.main([]) == 0
    good = env.state.read_text(encoding="utf-8")
    write_month(env.cache, "2026-09", SLIM_09 + b"5,Eps,J,2026-09-05,,why\n")
    if how == "exit_code":
        run.cfg.returncode = 2
    elif how == "timeout":
        run.cfg.error = subprocess.TimeoutExpired(cmd="wrangler", timeout=1)
    else:
        run.cfg.error = OSError("nope")
    assert publish_pages.main([]) == 1
    assert env.state.read_text(encoding="utf-8") == good


def test_missing_wrangler_fails_without_state(env, run, monkeypatch):
    monkeypatch.setenv("WRANGLER_BIN", str(env.cache / "no-such-wrangler"))
    assert publish_pages.main([]) == 1
    assert not run.calls and not env.state.exists()


def test_no_slim_csv_is_an_error(env, run):
    for f in env.cache.glob("*.csv"):
        f.unlink()
    assert publish_pages.main([]) == 1
    assert not run.calls and not env.state.exists()


# ---------- 설정 검증 ----------

@pytest.mark.parametrize("bad", [
    "http://demo-proj.pages.dev",
    "https://demo-proj.example.com",
    "https://demo-proj.pages.dev/",
    "https://demo-proj.pages.dev/csv",
    "https://Demo-Proj.pages.dev",
    "https://a.b.pages.dev",
    "https://evil.com/.pages.dev",
    "https://pages.dev",
])
def test_bad_base_url_is_rejected(env, run, monkeypatch, bad):
    monkeypatch.setenv("PAGES_BASE_URL", bad)
    assert publish_pages.main([]) == 2
    assert publish_pages.main(["--dry-run"]) == 2
    assert not run.calls and not env.state.exists()


@pytest.mark.parametrize("key", list(publish_pages.REQUIRED_KEYS))
def test_missing_config_exits_nonzero_naming_the_key(env, run, monkeypatch, capsys, key):
    monkeypatch.delenv(key)
    assert publish_pages.main([]) == 2
    err = capsys.readouterr().err
    assert key in err and TOKEN not in err and ACCOUNT not in err
    assert not run.calls and not env.state.exists()


def test_bad_project_name_is_rejected(env, run, monkeypatch):
    monkeypatch.setenv("PAGES_PROJECT", "--evil")
    assert publish_pages.main([]) == 2
    assert not run.calls


def test_dry_run_builds_staging_without_deploy_or_state(env, run, caplog, monkeypatch):
    caplog.set_level("INFO")
    for k in publish_pages.REQUIRED_KEYS:  # 설정이 없어도 계획은 볼 수 있다
        monkeypatch.delenv(k)
    assert publish_pages.main(["--dry-run"]) == 0
    assert not run.calls and not env.state.exists()
    assert (env.out / "_headers").exists()
    assert len(os.listdir(env.out / "csv")) == 2
    assert "dry-run" in caplog.text


# ---------- store.read_pages_state ----------

def test_read_pages_state_validates(env):
    def put(obj):
        env.state.write_text(obj if isinstance(obj, str) else json.dumps(obj), encoding="utf-8")
        return store.read_pages_state(str(env.cache))

    assert store.read_pages_state(str(env.cache)) is None  # 파일 없음
    assert put({"base": BASE, "months": {"2026-09": "aaa", "bad": "x", "2026-10": 5, "2026-11": ""}}) == {
        "base": BASE, "months": {"2026-09": "aaa"}}
    assert put("{not json") is None
    assert put("[1, 2]") is None
    assert put({"base": BASE, "months": []}) is None
    assert put({"base": BASE}) is None
    assert put({"base": "https://evil.example.com", "months": {"2026-09": "a"}}) is None
    assert put({"base": BASE + "\n", "months": {"2026-09": "a"}}) is None  # 끝 개행으로 정규식을 통과하지 못한다
    assert put({"base": 7, "months": {}}) is None


# ---------- /api/versions 의 pages ----------

@pytest.fixture
def api(env, monkeypatch):
    import server

    monkeypatch.setattr(server, "CACHE_DIR", str(env.cache))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]

    def versions():
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        c.request("GET", "/api/versions")
        resp = c.getresponse()
        body = resp.read()
        c.close()
        assert resp.status == 200 and resp.getheader("Cache-Control") == "no-cache"
        return json.loads(body)

    yield versions
    httpd.shutdown()
    httpd.server_close()


def current(env):
    return store.csv_versions(str(env.cache))


def test_versions_without_state_file_has_no_pages_key(env, api):
    body = api()
    assert body == {"months": current(env)}


def test_versions_pages_lists_current_months(env, api):
    env.state.write_text(json.dumps({"base": BASE, "months": current(env), "published_at": "x"}), encoding="utf-8")
    body = api()
    assert body["months"] == current(env)  # 기존 필드는 그대로
    assert body["pages"] == {"base": BASE, "months": current(env)}


def test_versions_pages_excludes_months_changed_since_publish(env, api):
    env.state.write_text(json.dumps({"base": BASE, "months": current(env)}), encoding="utf-8")
    write_month(env.cache, "2026-10", SLIM_10 + b"6,Zeta,J,2026-10-06,,why\n")  # 배포 뒤에 바뀐 달
    body = api()
    assert body["months"] == current(env)
    assert body["pages"] == {"base": BASE, "months": {"2026-09": sha12(SLIM_09)}}


def test_versions_pages_omitted_when_every_month_is_stale(env, api):
    env.state.write_text(json.dumps({"base": BASE, "months": {"2026-09": "old", "2026-10": "old"}}), encoding="utf-8")
    assert "pages" not in api()


@pytest.mark.parametrize("content", [
    "{not json",
    "[]",
    json.dumps({"base": BASE}),
    json.dumps({"base": BASE, "months": "nope"}),
    json.dumps({"base": "https://evil.example.com", "months": {"2026-09": "a"}}),
    json.dumps({"base": "http://demo-proj.pages.dev", "months": {"2026-09": "a"}}),
])
def test_versions_ignores_malformed_state(env, api, content):
    env.state.write_text(content, encoding="utf-8")
    assert api() == {"months": current(env)}
