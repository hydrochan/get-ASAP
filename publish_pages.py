#!/usr/bin/env python
"""월별 슬림 CSV 를 Cloudflare Pages(*.pages.dev)에 올린다 (cron 4시간).

공개 대시보드(get-asap.hydrochan.com)는 한국에서 Cloudflare LAX 로 연결돼 느리고 *.pages.dev 는 서울(ICN)에서 나간다.
그래서 가장 무거운 월별 슬림 CSV(cache/slim_papers_<월>.csv)를 Pages 에 `csv/<월>.<버전>.csv` 로 올려 두고,
/api/versions 가 "pages" 로 알려 주면 브라우저가 거기서 먼저 받는다. 올린 뒤 내용이 바뀐 달은 /api/versions 가 pages 목록에서
빼므로 원본에서 받는다(최신성은 refresh_csv 주기 그대로).

  python publish_pages.py             # 지난 배포와 달라진 게 있을 때만 배포
  python publish_pages.py --force     # 같아도 배포
  python publish_pages.py --dry-run   # staging(cache/pages_out)만 만들고 계획을 출력, 배포·상태 기록 없음

설정(.env 또는 환경변수): CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID, PAGES_PROJECT, PAGES_BASE_URL(https://<project>.pages.dev),
WRANGLER_BIN(wrangler 실행 파일 경로, 기본 .tools/node/bin/wrangler). 배포에 성공한 뒤에만 cache/pages_published.json 을 쓴다.
"""
import argparse
import datetime
import json
import logging
import os
import re
import shutil
import subprocess
import sys

import store

logger = logging.getLogger(__name__)

REQUIRED_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "PAGES_PROJECT", "PAGES_BASE_URL")
OPTIONAL_KEYS = ("WRANGLER_BIN",)
DEFAULT_WRANGLER = os.path.join(".tools", "node", "bin", "wrangler")
PROJECT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,57}$")  # Pages 프로젝트 이름 규칙(소문자·숫자·하이픈, 58자 이하)
MAX_FILE_BYTES = 25 * 1024 * 1024  # Pages 무료 플랜의 파일당 한도(25 MiB) - 넘는 달은 올리지 않고 원본에서 받게 둔다
DEPLOY_TIMEOUT_SEC = 600

# /csv/* 는 파일명에 버전이 들어 있어 내용이 바뀌면 이름이 바뀐다 -> 1년 immutable. CORS 는 대시보드(다른 출처)가 읽을 수 있게 연다.
# Pages 는 text/csv 를 압축하지 않아 text/plain 으로 내보내면 br 압축된다(2.5MB→0.69MB 실측). 클라이언트는 텍스트로 파싱하므로 타입과 무관.
HEADERS_TXT = (
    "/csv/*\n"
    "  Access-Control-Allow-Origin: *\n"
    "  Content-Type: text/plain; charset=utf-8\n"
    "  Cache-Control: public, max-age=31536000, immutable\n"
)
ROBOTS_TXT = "User-agent: *\nDisallow: /\n"


class DeployError(Exception):
    """wrangler 실행 실패(찾을 수 없음·시간 초과·비정상 종료)."""


def load_config(env=None) -> dict[str, str]:
    """설정값 {키: 값}. `import store` 가 config.py 를 불러 .env 를 환경변수로 올려 두므로(다른 스크립트와 같은 방식) 환경변수만 읽는다."""
    env = os.environ if env is None else env
    return {k: (env.get(k) or "").strip() for k in REQUIRED_KEYS + OPTIONAL_KEYS}


def build_staging(cache_dir: str, out_dir: str) -> dict[str, str]:
    """out_dir 을 비우고 새로 만든다: csv/<월>.<버전>.csv, _headers, robots.txt. 올라가는 {월: 버전} 을 돌려준다.

    버전은 여기서 읽은 바이트로 계산한다 - refresh_csv 가 그 사이 파일을 다시 써도 파일명과 내용이 어긋나지 않는다."""
    shutil.rmtree(out_dir, ignore_errors=True)
    csv_dir = os.path.join(out_dir, "csv")
    os.makedirs(csv_dir)
    months: dict[str, str] = {}
    for month in store.csv_versions(cache_dir):
        try:
            with open(store.slim_csv_path(month, cache_dir), "rb") as f:
                data = f.read()
        except OSError:
            logger.warning("슬림 CSV 를 읽지 못해 건너뜀: %s", month)
            continue
        if len(data) > MAX_FILE_BYTES:
            logger.warning("슬림 CSV 가 Pages 파일 한도(25 MiB)를 넘어 건너뜀: %s (%d bytes)", month, len(data))
            continue
        ver = store.slim_version_of(data)
        with open(os.path.join(csv_dir, f"{month}.{ver}.csv"), "wb") as f:
            f.write(data)
        months[month] = ver
    for name, text in (("_headers", HEADERS_TXT), ("robots.txt", ROBOTS_TXT)):
        with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    return months


def resolve_wrangler(value: str) -> str | None:
    """WRANGLER_BIN → 실행 파일 경로. 경로 구분자가 있으면 파일(상대경로는 프로젝트 루트 기준), 없으면 PATH 에서 찾는다."""
    value = value or DEFAULT_WRANGLER
    if "/" in value or os.sep in value:
        path = value if os.path.isabs(value) else os.path.join(store.PROJECT_ROOT, value)
        return path if os.path.isfile(path) else None
    return shutil.which(value)


def _redact(text: str, secrets: list[str]) -> str:
    for s in secrets:
        if s:
            text = text.replace(s, "***")
    return text


def deploy(cfg: dict[str, str], out_dir: str, cwd: str, message: str, timeout: int = DEPLOY_TIMEOUT_SEC) -> str:
    """`wrangler pages deploy` 를 셸 없이 실행한다. 성공하면 wrangler 출력의 마지막 줄(배포 주소)을 돌려주고, 실패하면 DeployError.
    토큰·계정 ID 는 환경변수로만 넘기고 로그에는 남기지 않는다."""
    wrangler = resolve_wrangler(cfg["WRANGLER_BIN"])
    if not wrangler:
        raise DeployError(f"wrangler 를 찾을 수 없음 (WRANGLER_BIN={cfg['WRANGLER_BIN'] or DEFAULT_WRANGLER})")
    env = dict(os.environ)
    env["CLOUDFLARE_API_TOKEN"] = cfg["CLOUDFLARE_API_TOKEN"]
    env["CLOUDFLARE_ACCOUNT_ID"] = cfg["CLOUDFLARE_ACCOUNT_ID"]
    # wrangler 는 `#!/usr/bin/env node` 스크립트라 cron 의 짧은 PATH 에서도 같은 폴더의 node 를 찾게 한다
    env["PATH"] = os.path.dirname(os.path.abspath(wrangler)) + os.pathsep + env.get("PATH", "")
    cmd = [wrangler, "pages", "deploy", out_dir, "--project-name", cfg["PAGES_PROJECT"], "--branch", "main",
           "--commit-dirty=true", "--commit-message", message]
    secrets = [cfg["CLOUDFLARE_API_TOKEN"], cfg["CLOUDFLARE_ACCOUNT_ID"]]
    try:
        proc = subprocess.run(cmd, env=env, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        raise DeployError(f"wrangler 시간 초과 ({timeout}초)") from None
    except OSError as e:
        raise DeployError(f"wrangler 실행 실패: {_redact(str(e), secrets)}") from None
    lines = _redact(f"{proc.stdout}\n{proc.stderr}", secrets).strip().splitlines()
    if proc.returncode != 0:
        tail = "\n".join(lines[-20:])
        raise DeployError(f"wrangler 종료 코드 {proc.returncode}\n{tail}")
    return lines[-1] if lines else ""


def write_state(cache_dir: str, base: str, months: dict[str, str], now: datetime.datetime) -> None:
    """배포 성공 상태를 원자적으로 쓴다(임시 파일 → os.replace). /api/versions 가 반쯤 쓴 파일을 읽지 않게 한다."""
    path = os.path.join(cache_dir, store.PAGES_STATE_NAME)
    tmp = f"{path}.{os.getpid()}.tmp"
    payload = {"base": base, "months": months, "published_at": now.isoformat(timespec="seconds")}
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def _parse_args(argv=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="get-ASAP 슬림 CSV -> Cloudflare Pages")
    parser.add_argument("--dry-run", action="store_true", help="staging 만 만들고 계획을 출력 (배포·상태 기록 없음)")
    parser.add_argument("--force", action="store_true", help="지난 배포와 같아도 배포")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cfg = load_config()
    if not args.dry_run:
        missing = [k for k in REQUIRED_KEYS if not cfg[k]]
        if missing:
            print(f"설정이 없습니다 (.env 또는 환경변수): {', '.join(missing)}", file=sys.stderr)
            return 2
    base = cfg["PAGES_BASE_URL"]
    if base and not store.PAGES_BASE_RE.match(base):
        print("PAGES_BASE_URL 은 https://<project>.pages.dev 형식이어야 합니다.", file=sys.stderr)
        return 2
    if cfg["PAGES_PROJECT"] and not PROJECT_RE.match(cfg["PAGES_PROJECT"]):
        print("PAGES_PROJECT 는 소문자·숫자·하이픈만 쓸 수 있습니다.", file=sys.stderr)
        return 2

    cache_dir = store.CACHE_DIR
    out_dir = os.path.join(cache_dir, "pages_out")
    months = build_staging(cache_dir, out_dir)
    if not months:
        logger.error("올릴 슬림 CSV 가 없습니다 (%s)", cache_dir)
        return 1
    state = store.read_pages_state(cache_dir)
    old = state["months"] if state else {}
    unchanged = bool(state) and old == months and (not base or state["base"] == base)
    changed = sorted(m for m, v in months.items() if old.get(m) != v)
    if unchanged and not args.force:
        logger.info("변경 없음 - 배포 생략 (%d개월)", len(months))
        return 0
    logger.info("배포 대상 %d개월, 달라진 달 %d개: %s", len(months), len(changed), ", ".join(changed) or "(없음)")
    if args.dry_run:
        logger.info("--dry-run: 배포·상태 기록 생략 (staging: %s)", out_dir)
        return 0

    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        last = deploy(cfg, out_dir, cache_dir, f"csv {now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    except DeployError as e:
        logger.error("Pages 배포 실패 - 상태 파일은 그대로 둠: %s", e)
        return 1
    try:
        write_state(cache_dir, base, months, now)
    except OSError:
        logger.exception("배포는 끝났지만 상태 파일을 쓰지 못함 (%s)", store.PAGES_STATE_NAME)
        return 1
    logger.info("Pages 배포 완료: %d개월 %s", len(months), last)
    return 0


if __name__ == "__main__":
    sys.exit(main())
