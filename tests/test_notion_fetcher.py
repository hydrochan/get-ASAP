"""notion_fetcher 단위 테스트 — Notion API는 mock 처리"""
import os
from datetime import date
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


PAPER_COLUMNS = ["title", "journal", "date", "url", "status", "gpt_reason"]


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
        assert list(df.columns) == PAPER_COLUMNS
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
        assert list(df.columns) == PAPER_COLUMNS


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


class TestRecentMonthRange:
    """캐시 강제 갱신 대상 월 범위 계산 테스트"""

    def test_default_includes_previous_and_current_month(self):
        from analytics.notion_fetcher import recent_month_range

        assert recent_month_range(date(2026, 5, 3)) == ("2026-04", "2026-05")

    def test_year_boundary(self):
        from analytics.notion_fetcher import recent_month_range

        assert recent_month_range(date(2026, 1, 2), lookback_months=1) == (
            "2025-12",
            "2026-01",
        )

    def test_zero_lookback_only_current_month(self):
        from analytics.notion_fetcher import recent_month_range

        assert recent_month_range(date(2026, 5, 3), lookback_months=0) == (
            "2026-05",
            "2026-05",
        )


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
            "gpt_reason": [""],
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


class TestFetchMonthPages:
    """월별 Notion 조회를 날짜 구간으로 나누는 로직 테스트"""

    def test_weekly_ranges(self):
        from analytics.notion_fetcher import _weekly_ranges

        ranges = _weekly_ranges("2026-02")

        assert len(ranges) == 4
        assert ranges[0] == ("2026-02-01", "2026-02-08")
        assert ranges[-1] == ("2026-02-22", "2026-03-01")

    @patch("analytics.notion_fetcher._fetch_pages")
    def test_fetch_month_pages_splits_by_day_plus_empty_date(self, mock_fetch_pages):
        from analytics.notion_fetcher import _fetch_month_pages

        mock_fetch_pages.return_value = []

        pages = _fetch_month_pages("db-2026-02", "2026-02")

        assert pages == []
        assert mock_fetch_pages.call_count == 7
        first_filter = mock_fetch_pages.call_args_list[0].args[1]
        assert first_filter == {"property": "Date", "date": {"before": "2026-02-01"}}
        first_weekly_filter = mock_fetch_pages.call_args_list[1].args[1]
        assert first_weekly_filter == {
            "and": [
                {"property": "Date", "date": {"on_or_after": "2026-02-01"}},
                {"property": "Date", "date": {"before": "2026-02-08"}},
            ]
        }
        after_month_filter = mock_fetch_pages.call_args_list[-2].args[1]
        assert after_month_filter == {
            "property": "Date",
            "date": {"on_or_after": "2026-03-01"},
        }
        empty_date_filter = mock_fetch_pages.call_args_list[-1].args[1]
        assert empty_date_filter == {"property": "Date", "date": {"is_empty": True}}

    @patch("analytics.notion_fetcher._fetch_pages")
    def test_fetch_month_pages_deduplicates_pages(self, mock_fetch_pages):
        from analytics.notion_fetcher import _fetch_month_pages

        mock_fetch_pages.side_effect = (
            [[{"id": "same"}, {"id": "before"}]]
            + [[] for _ in range(4)]
            + [[{"id": "same"}, {"id": "after"}], [{"id": "empty"}]]
        )

        pages = _fetch_month_pages("db-2026-02", "2026-02")

        assert [page["id"] for page in pages] == ["same", "before", "after", "empty"]

    def test_date_range_filter_shape(self):
        from analytics.notion_fetcher import _date_range_filter

        first_filter = _date_range_filter("2026-02-01", "2026-02-08")
        assert first_filter == {
            "and": [
                {"property": "Date", "date": {"on_or_after": "2026-02-01"}},
                {"property": "Date", "date": {"before": "2026-02-08"}},
            ]
        }


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
            "gpt_reason": [""],
        })

    def _api_pages(self, month):
        """API 응답용 가짜 page 목록"""
        return [_make_page(
            f"Fresh Paper {month}", "Science",
            f"{month}-15", "https://doi.org/fresh", "관심",
        )]

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_month_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_current_month_always_refetches(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_month, mock_parse, mock_save_cache,
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
            "gpt_reason": [""],
        })
        mock_fetch_month.return_value = [{"fake": "page"}]

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2026-04", "2026-04")

        # 현재 월이므로 _load_cache는 호출되지 않아야 함
        mock_load_cache.assert_not_called()
        # API fetch + parse + save가 호출되어야 함
        mock_fetch_month.assert_called_once_with("db-2026-04", "2026-04")
        mock_parse.assert_called_once()
        mock_save_cache.assert_called_once()
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Fresh Paper"

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_month_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_past_month_uses_cache(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_month, mock_parse, mock_save_cache,
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
        mock_fetch_month.assert_not_called()
        mock_parse.assert_not_called()
        assert len(df) == 1
        assert df.iloc[0]["title"] == "Cached Paper 2026-03"

    @patch("analytics.notion_fetcher._save_cache")
    @patch("analytics.notion_fetcher._parse_pages")
    @patch("analytics.notion_fetcher._fetch_month_pages")
    @patch("analytics.notion_fetcher._load_cache")
    @patch("analytics.notion_fetcher.find_monthly_dbs")
    @patch("analytics.notion_fetcher.date")
    @patch("analytics.notion_fetcher.config")
    def test_past_month_no_cache_fetches_api(
        self, mock_config, mock_date, mock_find_dbs,
        mock_load_cache, mock_fetch_month, mock_parse, mock_save_cache,
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
            "gpt_reason": [""],
        })
        mock_fetch_month.return_value = [{"fake": "page"}]

        from analytics.notion_fetcher import fetch_papers
        df = fetch_papers("2026-02", "2026-02")

        mock_load_cache.assert_called_once_with("2026-02")
        mock_fetch_month.assert_called_once_with("db-2026-02", "2026-02")
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
        assert list(df.columns) == PAPER_COLUMNS

    @patch("analytics.notion_fetcher.config")
    def test_missing_parent_page_id_raises(self, mock_config):
        """NOTION_PARENT_PAGE_ID가 없으면 ValueError"""
        mock_config.NOTION_PARENT_PAGE_ID = ""

        from analytics.notion_fetcher import fetch_papers
        with pytest.raises(ValueError, match="NOTION_PARENT_PAGE_ID"):
            fetch_papers("2026-01", "2026-03")
