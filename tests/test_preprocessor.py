"""preprocessor 단위 테스트 — TF-IDF 키워드 추출"""
import pandas as pd
import pytest


class TestCleanTitle:
    def test_lowercase_and_strip(self):
        from analytics.preprocessor import clean_title
        assert clean_title("  Oxygen Evolution Reaction  ") == "oxygen evolution reaction"

    def test_remove_special_chars(self):
        from analytics.preprocessor import clean_title
        assert clean_title("TiO₂-based (photo)catalysis: a review") == "tio2based photocatalysis a review"

    def test_empty_string(self):
        from analytics.preprocessor import clean_title
        assert clean_title("") == ""


class TestRemoveStopwords:
    def test_removes_academic_stopwords(self):
        from analytics.preprocessor import remove_stopwords
        result = remove_stopwords("a novel study of high performance catalysts using advanced methods")
        assert "novel" not in result
        assert "study" not in result
        assert "catalysts" in result

    def test_preserves_meaningful_words(self):
        from analytics.preprocessor import remove_stopwords
        result = remove_stopwords("oxygen evolution reaction perovskite electrode")
        assert "oxygen" in result
        assert "perovskite" in result


class TestExtractKeywords:
    def test_basic_extraction(self):
        from analytics.preprocessor import extract_keywords
        titles = [
            "oxygen evolution reaction on perovskite",
            "oxygen evolution catalyst design",
            "carbon dioxide reduction mechanism",
            "carbon dioxide electrochemical conversion",
            "perovskite solar cell efficiency",
        ]
        df = pd.DataFrame({"title": titles})
        keywords = extract_keywords(df, top_n=5)
        assert len(keywords) <= 5
        assert all(isinstance(kw, tuple) and len(kw) == 2 for kw in keywords)
        keyword_names = [kw[0] for kw in keywords]
        assert any("oxygen" in kw for kw in keyword_names)

    def test_empty_dataframe(self):
        from analytics.preprocessor import extract_keywords
        df = pd.DataFrame({"title": []})
        keywords = extract_keywords(df, top_n=10)
        assert keywords == []

    def test_top_n_limit(self):
        from analytics.preprocessor import extract_keywords
        titles = [f"keyword{i} paper title" for i in range(20)]
        df = pd.DataFrame({"title": titles})
        keywords = extract_keywords(df, top_n=3)
        assert len(keywords) <= 3


class TestMonthlyKeywords:
    def test_monthly_grouping(self):
        from analytics.preprocessor import extract_keywords_by_month
        df = pd.DataFrame({
            "title": [
                "oxygen evolution catalyst",
                "oxygen reduction reaction",
                "carbon capture method",
                "carbon dioxide conversion",
            ],
            "date": pd.to_datetime([
                "2026-01-15", "2026-01-20",
                "2026-02-10", "2026-02-15",
            ]),
        })
        result = extract_keywords_by_month(df, top_n=3)
        assert "2026-01" in result
        assert "2026-02" in result
        assert len(result) == 2
