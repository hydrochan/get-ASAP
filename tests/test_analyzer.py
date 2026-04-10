"""analyzer 단위 테스트 — 4가지 분석 로직"""
import pandas as pd
import pytest


def _sample_df():
    """테스트용 샘플 DataFrame"""
    return pd.DataFrame({
        "title": [
            "oxygen evolution reaction on perovskite",
            "oxygen evolution catalyst nickel iron",
            "carbon dioxide reduction copper",
            "carbon dioxide electrochemical conversion",
            "perovskite solar cell efficiency",
            "lithium ion battery cathode material",
            "hydrogen evolution reaction platinum",
            "oxygen reduction reaction carbon",
        ],
        "journal": [
            "ACS Catalysis", "Nature Catalysis",
            "ACS Catalysis", "Joule",
            "Advanced Materials", "ACS Energy Letters",
            "Nature Catalysis", "ACS Catalysis",
        ],
        "date": pd.to_datetime([
            "2026-01-10", "2026-01-15",
            "2026-01-20", "2026-02-05",
            "2026-02-10", "2026-02-15",
            "2026-03-01", "2026-03-10",
        ]),
        "url": [""] * 8,
        "status": [
            "대기중", "관련", "대기중", "관련",
            "대기중", "다운완료", "대기중", "대기중",
        ],
    })


class TestJournalFrequency:
    def test_basic_frequency(self):
        from analytics.analyzer import journal_frequency
        df = _sample_df()
        result = journal_frequency(df)
        assert result.iloc[0] == 3  # ACS Catalysis가 3건으로 최다
        assert result.index[0] == "ACS Catalysis"

    def test_monthly_frequency(self):
        from analytics.analyzer import journal_monthly_frequency
        df = _sample_df()
        result = journal_monthly_frequency(df)
        assert "ACS Catalysis" in result.columns
        assert "2026-01" in result.index


class TestKeywordTrend:
    def test_trend_structure(self):
        from analytics.analyzer import keyword_trend
        df = _sample_df()
        result = keyword_trend(df, top_n=5)
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) <= 5


class TestJournalKeywordCrosstab:
    def test_crosstab_structure(self):
        from analytics.analyzer import journal_keyword_crosstab
        df = _sample_df()
        result = journal_keyword_crosstab(df, top_n_keywords=5)
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) <= 5


class TestInterestAnalysis:
    def test_interest_ratio_by_journal(self):
        from analytics.analyzer import interest_ratio_by_journal
        df = _sample_df()
        result = interest_ratio_by_journal(df)
        assert "journal" in result.columns
        assert "ratio" in result.columns
        nc = result[result["journal"] == "Nature Catalysis"]
        assert nc.iloc[0]["ratio"] == 0.5

    def test_interest_keywords(self):
        from analytics.analyzer import interest_keywords
        df = _sample_df()
        all_kw, interest_kw = interest_keywords(df, top_n=5)
        assert isinstance(all_kw, list)
        assert isinstance(interest_kw, list)
