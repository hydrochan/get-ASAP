from pathlib import Path


def test_global_heatmap_uses_same_minimum_keyword_limit_as_per_row():
    html = Path("dashboard/index.html").read_text(encoding="utf-8")

    assert "slice(0, Math.max(kwLimit, 25)).map(e=>e[0])" in html
