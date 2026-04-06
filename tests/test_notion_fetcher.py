"""notion_fetcher 단위 테스트 — Notion API는 mock 처리"""
import os
from datetime import date
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


class TestFetchPapers:
    """fetch_papers: 캐싱 로직 포함 메인 진입점 테스트"""

    def _cached_df(self, month):
        """캐시용 가짜 DataFrame 생성"""
        return pd.DataFrame({
            "title": [f"Cached Paper {month}"],
            "journal": ["Nature"],
            "date": pd.to_datetime([f"{month}-01"]),
            "url": ["https://example.com"],
            "status": ["대기중"],
        })

    def _api_pages(self, month):
        """API 응답용 가짜 page 목록"""
        return [_make_page(
            f"Fresh Paper {month}", "Science",
            f"{month}-15", "https://doi.org/fresh", "관심",
        )]

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_all_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_current_month_always_refetches(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_all, mock_parse, mock_save_cache,
    ):
        """현재 월은 캐시가 있어도 항상 API에서 re-fetch"""
        mock_config.NOTION_PARENT_PAGE_ID = "parent-id"
        # 현재 월을 2026-04로 고정
        mock_date.today.return_value = date(2026, 4, 7)

        mock_find_dbs.return_value = {"2026-04": "db-2026-04"}
        mock_parse.return_value = pd.DataFrame({
            "title": ["Fresh Paper"],
            "journal": ["Science"],
            "date": pd.to_datetime(["2026-04-15"]),
            "url": ["https://doi.org/fresh"],
            "status": ["관심"],
        })
        mock_fetch_all.return_value = [{"fake": "page"}]

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2026-04", "2026-04")

        # 현재 월이므로 _load_cache는 호출되지 않아야 함
        mock_load_cache.assert_not_called()
        # API fetch + parse + save가 호출되어야 함
        mock_fetch_all.assert_called_once_with("db-2026-04")
        mock_parse.assert_called_once()
        mock_save_cache.assert_called_once()
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Fresh Paper"

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_all_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_past_month_uses_cache(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_all, mock_parse, mock_save_cache,
    ):
        """과거 월은 캐시가 있으면 API 호출하지 않음"""
        mock_config.NOTION_PARENT_PAGE_ID = "parent-id"
        mock_date.today.return_value = date(2026, 4, 7)

        mock_find_dbs.return_value = {"2026-03": "db-2026-03"}
        cached = self._cached_df("2026-03")
        mock_load_cache.return_value = cached

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2026-03", "2026-03")

        # 캐시에서 로드하므로 API fetch 안 함
        mock_load_cache.assert_called_once_with("2026-03")
        mock_fetch_all.assert_not_called()
        mock_parse.assert_not_called()
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Cached Paper 2026-03"

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_all_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_past_month_no_cache_fetches_api(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_all, mock_parse, mock_save_cache,
    ):
        """과거 월이라도 캐시가 없으면 API에서 fetch"""
        mock_config.NOTION_PARENT_PAGE_ID = "parent-id"
        mock_date.today.return_value = date(2026, 4, 7)

        mock_find_dbs.return_value = {"2026-02": "db-2026-02"}
        mock_load_cache.return_value = None  # 캐시 없음
        mock_parse.return_value = pd.DataFrame({
            "title": ["API Paper"],
            "journal": ["ACS Catalysis"],
            "date": pd.to_datetime(["2026-02-10"]),
            "url": ["https://doi.org/api"],
            "status": ["대기중"],
        })
        mock_fetch_all.return_value = [{"fake": "page"}]

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2026-02", "2026-02")

        mock_load_cache.assert_called_once_with("2026-02")
        mock_fetch_all.assert_called_once_with("db-2026-02")
        mock_save_cache.assert_called_once()
        assert len(df) == 1

    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_no_dbs_returns_empty(self, mock_config, mock_date, mock_find_dbs):
        """DB가 없는 기간이면 빈 DataFrame 반환"""
        mock_config.NOTION_PARENT_PAGE_ID = "parent-id"
        mock_date.today.return_value = date(2026, 4, 7)
        mock_find_dbs.return_value = {}

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2025-01", "2025-01")

        assert len(df) == 0
        assert list(df.columns) == ["title", "journal", "date", "url", "status"]

    @patch("analytics.notion_fetcher.config")
    def test_missing_parent_page_id_raises(self, mock_config):
        """NOTION_PARENT_PAGE_ID가 없으면 ValueError"""
        mock_config.NOTION_PARENT_PAGE_ID = ""

        from analytics.notion_fetcher import fetch_papers
        with pytest.raises(ValueError, match="NOTION_PARENT_PAGE_ID"):
            fetch_papers("2026-01", "2026-03")
