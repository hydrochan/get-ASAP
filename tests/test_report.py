"""report 단위 테스트 — 마크다운 리포트 생성"""
import pandas as pd
import pytest


def _sample_df():
    return pd.DataFrame({
        "title": [
            "oxygen evolution reaction on perovskite",
            "carbon dioxide reduction copper",
            "perovskite solar cell efficiency",
        ],
        "journal": ["ACS Catalysis", "Nature Catalysis", "Advanced Materials"],
        "date": pd.to_datetime(["2026-01-10", "2026-01-15", "2026-02-10"]),
        "url": ["", "", ""],
        "status": ["대기중", "관심", "대기중"],
    })


class TestGenerateReport:
    def test_report_contains_sections(self):
        from analytics.report import generate_report
        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")
        assert "# get-ASAP 논문 분석 리포트" in md
        assert "키워드 트렌드" in md
        assert "저널 통계" in md

    def test_report_has_date_range(self):
        from analytics.report import generate_report
        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")
        assert "2026-01" in md
        assert "2026-02" in md

    def test_save_report(self, tmp_path):
        from analytics.report import generate_report, save_report
        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")
        path = save_report(md, "2026-01", "2026-02", reports_dir=str(tmp_path))
        assert path.endswith(".md")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "get-ASAP" in content
