"""notion_fetcher 단위 테스트 — Notion API는 mock 처리"""
import os
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def _make_page(title, journal, date, url, status):
    """Notion API 응답 형태의 가짜 page 생성"""
    return {
        "properties": {
            "Title": {"title": [{"plain_text": title}]},
            "Journal": {"select": {"name": journal} if journal else None},
            "Date": {"date": {"start": date} if date else None},
            "URL": {"url": url if url else None},
            "Status": {"select": {"name": status} if status else None},
        }
    }


class TestParsePages:
    """_parse_pages: Notion API 응답 → DataFrame 변환 테스트"""

    def test_basic_conversion(self):
        from analytics.notion_fetcher import _parse_pages

        pages = [
            _make_page(
                "Oxygen Evolution on Perovskite",
                "ACS Catalysis",
                "2026-03-15",
                "https://doi.org/10.1021/acscatal.123",
                "대기중",
            ),
            _make_page(
                "Carbon Dioxide Reduction Review",
                "Nature Catalysis",
                "2026-03-16",
                "https://doi.org/10.1038/s41929-123",
                "관심",
            ),
        ]
        df = _parse_pages(pages)

        assert len(df) == 2
        assert list(df.columns) == ["title", "journal", "date", "url", "status"]
        assert df.iloc[0]["title"] == "Oxygen Evolution on Perovskite"
        assert df.iloc[0]["journal"] == "ACS Catalysis"
        assert df.iloc[1]["status"] == "관심"
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_missing_optional_fields(self):
        from analytics.notion_fetcher import _parse_pages

        pages = [_make_page("Title Only Paper", "", "", "", "")]
        df = _parse_pages(pages)

        assert len(df) == 1
        assert df.iloc[0]["title"] == "Title Only Paper"
        assert df.iloc[0]["journal"] == ""

    def test_empty_pages(self):
        from analytics.notion_fetcher import _parse_pages

        df = _parse_pages([])
        assert len(df) == 0
        assert list(df.columns) == ["title", "journal", "date", "url", "status"]


class TestFindMonthlyDbs:
    """find_monthly_dbs: 기간 내 월별 DB ID 목록 반환 테스트"""

    @patch("analytics.notion_fetcher._find_monthly_db")
    def test_range_lookup(self, mock_find):
        from analytics.notion_fetcher import find_monthly_dbs

        mock_find.side_effect = lambda pid, m: (
            f"db-{m}" if m in ("2026-01", "2026-03") else None
        )
        result = find_monthly_dbs("parent-id", "2026-01", "2026-03")

        assert result == {"2026-01": "db-2026-01", "2026-03": "db-2026-03"}
        assert mock_find.call_count == 3


class TestCaching:
    """CSV 캐싱 동작 테스트"""

    def test_save_and_load_cache(self, tmp_path):
        from analytics.notion_fetcher import _save_cache, _load_cache

        df = pd.DataFrame({
            "title": ["Test Paper"],
            "journal": ["Nature"],
            "date": pd.to_datetime(["2026-03-15"]),
            "url": ["https://example.com"],
            "status": ["대기중"],
        })
        _save_cache(df, "2026-03", cache_dir=str(tmp_path))
        loaded = _load_cache("2026-03", cache_dir=str(tmp_path))

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded.iloc[0]["title"] == "Test Paper"

    def test_load_missing_cache(self, tmp_path):
        from analytics.notion_fetcher import _load_cache

        result = _load_cache("2099-01", cache_dir=str(tmp_path))
        assert result is None
