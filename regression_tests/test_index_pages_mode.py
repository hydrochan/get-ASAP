"""dashboard/index.html 의 Pages 모드(공개 화면을 *.pages.dev 에서 서빙) 클라이언트 동작 회귀 테스트.

브라우저 없이 Node 로 index.html 인라인 스크립트에서 함수 원문을 뽑아 가짜 fetch 로 돌린다(regression_tests/pages_mode_driver.js).
  Pages 모드 판별(호스트가 아니라 publish_pages.py 가 staging 사본에 넣는 ASAP_STATIC_FRONT 표시) / apiUrl·pubFetch / paperHref 절대 링크 / versions.json 으로 첫 화면 -> 원본 버전과 다른 달만 다시 받기 /
  원본 실패 시 pages 데이터 유지 / stale 로드 가드 / /api/session 호출 없음 + 방문 신호 한 번 / 관리자 로그인은 원본으로 / 구문 검사
Node 가 없으면 건너뛴다.
"""
import os
import shutil
import subprocess

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
DRIVER = os.path.join(HERE, "pages_mode_driver.js")
INDEX = os.path.join(os.path.dirname(HERE), "dashboard", "index.html")
NODE = shutil.which("node")

pytestmark = pytest.mark.skipif(NODE is None, reason="node 가 없음")

SCENARIOS = [
    "pages mode detection",
    "marker true on a custom domain drives the pages flow with the injected api base",
    "marker false on a pages dev like origin stays in origin mode",
    "apiUrl and pubFetch",
    "paperHref absolute in pages mode",
    "first paint from versions.json then only changed months from origin",
    "same versions means no origin csv fetch",
    "origin versions failure keeps pages data silently",
    "origin month fetch failure keeps the pages rows",
    "stale load is not touched by reconcile",
    "versions.json failure falls back to origin versions",
    "origin mode never touches versions.json",
    "no session call and one visit beacon in pages mode",
    "admin login hash goes to origin in pages mode",
    "inline scripts parse and no raw api fetch remains",
]


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_pages_mode(scenario):
    proc = subprocess.run([NODE, DRIVER, INDEX, scenario], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "OK"


def test_feedback_post_is_cors_simple_in_pages_mode():
    """피드백 제출은 Pages 모드에서 text/plain(preflight 없음)으로 보낸다 - 서버는 Content-Type 과 무관하게 JSON 으로 읽는다."""
    html = open(INDEX, encoding="utf-8").read()
    assert "'Content-Type': PAGES_MODE ? 'text/plain;charset=UTF-8' : 'application/json'" in html
    assert "fetch(apiUrl('/api/feedback'), {\n          method: 'POST'" in html.replace("\r\n", "\n")
