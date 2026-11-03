"""dashboard/index.html 이 쓰는 Tailwind CSS 를 정적 파일로 빌드한다 (Play CDN 대체).

  .\\.venv\\Scripts\\python.exe build_css.py

1) .tools/tailwindcss-windows-x64.exe (v3.4.17 standalone CLI) 로 dashboard/static/tailwind.css 를 --minify 빌드
2) 내용 해시(sha256 앞 10자리)를 계산
3) index.html 의 <link rel="stylesheet" href="/static/tailwind.css?v=..."> 의 v 값만 갈아 끼운다
   (줄바꿈·나머지 내용은 그대로 — newline='' 로 읽고 쓴다)

운영 VM 은 git pull + 재시작만 하므로 빌드 결과(dashboard/static/tailwind.css)와 갱신된 index.html 을 함께 커밋해야 한다.
클래스를 추가·변경한 뒤에는 이 스크립트를 다시 돌릴 것. CLI 는 .tools/ (gitignore) 에 직접 내려받아 둔다:
https://github.com/tailwindlabs/tailwindcss/releases/download/v3.4.17/tailwindcss-windows-x64.exe
"""
import hashlib
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
DASH = ROOT / "dashboard"
CLI = ROOT / ".tools" / "tailwindcss-windows-x64.exe"
HTML = DASH / "index.html"
OUT = DASH / "static" / "tailwind.css"
LINK_RE = re.compile(r'(<link rel="stylesheet" href="/static/tailwind\.css\?v=)[0-9a-f]*(">)')


def main() -> int:
    if not CLI.is_file():
        print(f"Tailwind CLI 없음: {CLI}", file=sys.stderr)
        return 1
    OUT.parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(CLI), "-c", str(DASH / "tailwind.config.js"), "-i", str(DASH / "tailwind.input.css"),
           "-o", str(OUT), "--minify"]
    done = subprocess.run(cmd, cwd=ROOT)
    if done.returncode != 0:
        print("Tailwind 빌드 실패", file=sys.stderr)
        return done.returncode

    version = hashlib.sha256(OUT.read_bytes()).hexdigest()[:10]
    with open(HTML, encoding="utf-8", newline="") as f:
        html = f.read()
    new_html, n = LINK_RE.subn(rf"\g<1>{version}\g<2>", html)
    if n != 1:
        print(f'index.html 에서 <link rel="stylesheet" href="/static/tailwind.css?v=..."> 를 {n}개 찾았다 (1개여야 함)', file=sys.stderr)
        return 1
    if new_html != html:
        with open(HTML, "w", encoding="utf-8", newline="") as f:
            f.write(new_html)
    print(f"{OUT.relative_to(ROOT)}: {OUT.stat().st_size:,} bytes, v={version}"
          f" ({'index.html 갱신' if new_html != html else 'index.html 변경 없음'})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
