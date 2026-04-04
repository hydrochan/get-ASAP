"""notion_client_mod 모듈 테스트 (NOTION-01, NOTION-02, NOTION-03)

TDD RED: notion_client_mod.py 미존재 → 모든 테스트 ImportError로 실패
"""
import logging
import pytest
from unittest.mock import MagicMock, patch, call

import config
from models import PaperMetadata


def make_paper(**kwargs) -> PaperMetadata:
    """테스트용 PaperMetadata 헬퍼 함수"""
    defaults = {
        "title": "Test Paper",
        "doi": "10.1021/test.2025",
        "journal": "JACS",
        "date": "2025-01-15",
    }
    defaults.update(kwargs)
    return PaperMetadata(**defaults)


# ─── DB 생성 관련 테스트 ───────────────────────────────────────────────────────

class TestCreatePaperDb:
    """create_paper_db — Notion DB 생성 (NOTION-01)"""

    def test_create_paper_db(self):
        """databases.create 호출 시 initial_data_source.properties에 7개 속성 포함, title="get-ASAP Papers" (D-01, D-02)"""
        from notion_client_mod import create_paper_db

        mock_client = MagicMock()
        mock_client.databases.create.return_value = {"id": "new-db-id-789"}

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            result = create_paper_db("parent-page-uuid")

        assert result == "new-db-id-789"

        call_kwargs = mock_client.databases.create.call_args
        assert call_kwargs is not None, "databases.create가 호출되지 않았다"

        kwargs = call_kwargs.kwargs if call_kwargs.kwargs else call_kwargs[1]

        # title 확인
        title = kwargs.get("title", [])
        title_text = title[0]["text"]["content"] if title else ""
        assert title_text == "get-ASAP Papers", f"DB 제목이 잘못됨: {title_text}"

        # initial_data_source.properties 확인
        initial_ds = kwargs.get("initial_data_source", {})
        props = initial_ds.get("properties", {})
        expected_keys = {"Title", "DOI", "Journal", "Date", "Status", "URL", "Authors"}
        assert expected_keys == set(props.keys()), f"속성 키 불일치: {set(props.keys())}"


class TestGetOrCreateDb:
    """get_or_create_db — 환경변수 기반 DB 획득/생성 (D-08)"""

    def test_get_or_create_db_uses_existing(self, monkeypatch):
        """NOTION_DATABASE_ID 환경변수 있으면 그 값 반환 (D-08)"""
        from notion_client_mod import get_or_create_db
        monkeypatch.setattr(config, "NOTION_DATABASE_ID", "existing-db-id-123")

        with patch("notion_client_mod.create_paper_db") as mock_create:
            result = get_or_create_db()

        assert result == "existing-db-id-123"
        mock_create.assert_not_called()

    def test_get_or_create_db_creates_new(self, monkeypatch):
        """NOTION_DATABASE_ID 없으면 create_paper_db 호출 (D-08)"""
        from notion_client_mod import get_or_create_db
        monkeypatch.setattr(config, "NOTION_DATABASE_ID", None)
        monkeypatch.setattr(config, "NOTION_PARENT_PAGE_ID", "parent-page-uuid")

        with patch("notion_client_mod.create_paper_db", return_value="created-db-id") as mock_create:
            result = get_or_create_db()

        assert result == "created-db-id"
        mock_create.assert_called_once_with("parent-page-uuid")

    def test_get_or_create_db_no_parent_raises(self, monkeypatch):
        """NOTION_DATABASE_ID도 NOTION_PARENT_PAGE_ID도 없으면 ValueError"""
        from notion_client_mod import get_or_create_db
        monkeypatch.setattr(config, "NOTION_DATABASE_ID", None)
        monkeypatch.setattr(config, "NOTION_PARENT_PAGE_ID", None)

        with pytest.raises(ValueError):
            get_or_create_db()


# ─── data_source_id 획득 ─────────────────────────────────────────────────────

class TestGetDataSourceId:
    """_get_data_source_id — databases.retrieve로 data_source_id 획득"""

    def test_get_data_source_id(self):
        """databases.retrieve 호출 후 data_sources[0]["id"] 반환"""
        from notion_client_mod import _get_data_source_id

        mock_client = MagicMock()
        mock_client.databases.retrieve.return_value = {
            "id": "db-id-123",
            "data_sources": [{"id": "ds-id-456"}],
        }

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            result = _get_data_source_id("db-id-123")

        assert result == "ds-id-456"
        mock_client.databases.retrieve.assert_called_once_with("db-id-123")

    def test_get_data_source_id_empty_raises(self):
        """data_sources가 빈 배열이면 ValueError"""
        from notion_client_mod import _get_data_source_id

        mock_client = MagicMock()
        mock_client.databases.retrieve.return_value = {
            "id": "db-id-123",
            "data_sources": [],
        }

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            with pytest.raises(ValueError):
                _get_data_source_id("db-id-123")


# ─── 속성 변환 테스트 ────────────────────────────────────────────────────────

class TestBuildProperties:
    """_build_properties — PaperMetadata → Notion properties 딕셔너리 변환"""

    def test_build_properties(self):
        """PaperMetadata → Notion properties 딕셔너리 변환 (필수 4필드 + 선택 필드)"""
        from notion_client_mod import _build_properties

        paper = make_paper(
            authors=["Kim, J.", "Lee, S."],
            url="https://pubs.acs.org/doi/10.1021/test.2025",
        )
        props = _build_properties(paper)

        # 필수 필드 확인
        assert "Title" in props
        assert props["Title"]["title"][0]["text"]["content"] == "Test Paper"

        assert "DOI" in props
        assert props["DOI"]["rich_text"][0]["text"]["content"] == "10.1021/test.2025"

        assert "Journal" in props
        assert props["Journal"]["select"]["name"] == "JACS"

        assert "Status" in props
        assert props["Status"]["select"]["name"] == "대기중"

        assert "Date" in props
        assert props["Date"]["date"]["start"] == "2025-01-15"

        # 선택 필드 확인
        assert "URL" in props
        assert props["URL"]["url"] == "https://pubs.acs.org/doi/10.1021/test.2025"

        assert "Authors" in props
        assert "Kim, J." in props["Authors"]["rich_text"][0]["text"]["content"]

    def test_build_properties_optional_fields_none(self):
        """authors=None, url=None일 때 해당 필드 미포함"""
        from notion_client_mod import _build_properties

        paper = make_paper(authors=None, url=None)
        props = _build_properties(paper)

        assert "URL" not in props
        assert "Authors" not in props
        # 필수 필드는 여전히 포함
        assert "Title" in props
        assert "DOI" in props
        assert "Journal" in props
        assert "Status" in props


# ─── 논문 저장 테스트 ────────────────────────────────────────────────────────

class TestSavePaper:
    """save_paper — 단일 논문 저장 (NOTION-02, NOTION-03, D-09)"""

    def _make_client_no_dup(self):
        """중복 없는 mock client"""
        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {
            "results": [],
            "has_more": False,
        }
        mock_client.pages.create.return_value = {"id": "new-page-id"}
        return mock_client

    def _make_client_with_dup(self):
        """중복 있는 mock client"""
        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {
            "results": [{"id": "existing-page-id"}],
            "has_more": False,
        }
        return mock_client

    def test_save_paper_creates_page(self):
        """중복 아닌 논문 → pages.create 호출, True 반환 (D-09)"""
        from notion_client_mod import save_paper

        paper = make_paper()
        mock_client = self._make_client_no_dup()

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            result = save_paper(paper, "db-id-123", "ds-id-456")

        assert result is True
        mock_client.pages.create.assert_called_once()
        create_kwargs = mock_client.pages.create.call_args.kwargs
        assert create_kwargs["parent"]["database_id"] == "db-id-123"

    def test_save_paper_status_default(self):
        """저장 시 Status가 "대기중"으로 설정됨"""
        from notion_client_mod import save_paper

        paper = make_paper()
        mock_client = self._make_client_no_dup()

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            save_paper(paper, "db-id-123", "ds-id-456")

        create_kwargs = mock_client.pages.create.call_args.kwargs
        status = create_kwargs["properties"]["Status"]["select"]["name"]
        assert status == "대기중"

    def test_save_paper_skips_duplicate_doi(self):
        """DOI 일치하는 기존 페이지 있으면 False 반환 + logging.info (D-03, D-05)"""
        from notion_client_mod import save_paper

        paper = make_paper()
        mock_client = self._make_client_with_dup()

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            with patch("notion_client_mod.logging") as mock_log:
                result = save_paper(paper, "db-id-123", "ds-id-456")

        assert result is False
        mock_client.pages.create.assert_not_called()
        mock_log.info.assert_called()

    def test_save_paper_skips_duplicate_title(self):
        """DOI 빈 문자열 + 제목 일치 시 False 반환 (D-04)"""
        from notion_client_mod import save_paper

        paper = make_paper(doi="")  # DOI 없음 → 제목 기반 중복 검사
        mock_client = self._make_client_with_dup()

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            result = save_paper(paper, "db-id-123", "ds-id-456")

        # 제목 기반 필터 사용 확인
        query_call = mock_client.data_sources.query.call_args
        filter_arg = query_call.kwargs.get("filter") or query_call[1].get("filter") or query_call[0][1]
        assert "title" in filter_arg, f"제목 기반 필터 사용 안 함: {filter_arg}"
        assert result is False


# ─── 배치 저장 테스트 ────────────────────────────────────────────────────────

class TestSavePapers:
    """save_papers — 배치 저장 (D-10)"""

    def test_save_papers_batch(self):
        """3개 논문 리스트 → 각각 save_paper 호출, 결과 집계 (D-10)"""
        from notion_client_mod import save_papers

        papers = [
            make_paper(doi="10.1021/a", title="Paper A"),
            make_paper(doi="10.1021/b", title="Paper B"),
            make_paper(doi="10.1021/c", title="Paper C"),
        ]

        mock_client = MagicMock()
        mock_client.databases.retrieve.return_value = {
            "id": "db-id-123",
            "data_sources": [{"id": "ds-id-456"}],
        }
        # 1번: 저장 성공, 2번: 중복(스킵), 3번: 저장 성공
        mock_client.data_sources.query.side_effect = [
            {"results": [], "has_more": False},           # 1번 중복 없음
            {"results": [{"id": "dup"}], "has_more": False},  # 2번 중복 있음
            {"results": [], "has_more": False},           # 3번 중복 없음
        ]
        mock_client.pages.create.return_value = {"id": "new-page"}

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            result = save_papers(papers, "db-id-123")

        assert result["saved"] == 2
        assert result["skipped"] == 1
        assert result["failed"] == 0

    def test_save_papers_data_source_id_cached(self):
        """save_papers 내에서 databases.retrieve 1회만 호출"""
        from notion_client_mod import save_papers

        papers = [
            make_paper(doi="10.1021/a", title="Paper A"),
            make_paper(doi="10.1021/b", title="Paper B"),
        ]

        mock_client = MagicMock()
        mock_client.databases.retrieve.return_value = {
            "id": "db-id-123",
            "data_sources": [{"id": "ds-id-456"}],
        }
        mock_client.data_sources.query.return_value = {"results": [], "has_more": False}
        mock_client.pages.create.return_value = {"id": "new-page"}

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            save_papers(papers, "db-id-123")

        # databases.retrieve는 1회만 호출되어야 함 (캐싱)
        assert mock_client.databases.retrieve.call_count == 1


# ─── 에러 처리 테스트 ────────────────────────────────────────────────────────

class TestErrorHandling:
    """에러 처리 (D-11, D-12)"""

    def test_rate_limit_retry(self):
        """rate_limited 에러 → 1초 sleep 후 재시도 성공 (D-12)"""
        from notion_client import APIResponseError
        from notion_client_mod import save_paper

        paper = make_paper()

        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {"results": [], "has_more": False}

        rate_limit_error = APIResponseError("rate_limited", 429, "Rate limited", MagicMock(), "")
        # 첫 번째 pages.create → rate_limited, 두 번째 → 성공
        mock_client.pages.create.side_effect = [
            rate_limit_error,
            {"id": "new-page-id"},
        ]

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            with patch("notion_client_mod.time.sleep") as mock_sleep:
                result = save_paper(paper, "db-id-123", "ds-id-456")

        assert result is True
        mock_sleep.assert_called_once_with(1)
        assert mock_client.pages.create.call_count == 2

    def test_rate_limit_retry_fail(self):
        """재시도도 실패 → warning 로그 + False 반환 (D-12)"""
        from notion_client import APIResponseError
        from notion_client_mod import save_paper

        paper = make_paper()

        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {"results": [], "has_more": False}

        rate_limit_error = APIResponseError("rate_limited", 429, "Rate limited", MagicMock(), "")
        mock_client.pages.create.side_effect = [rate_limit_error, rate_limit_error]

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            with patch("notion_client_mod.time.sleep"):
                with patch("notion_client_mod.logging") as mock_log:
                    result = save_paper(paper, "db-id-123", "ds-id-456")

        assert result is False
        mock_log.warning.assert_called()

    def test_api_error_warning_and_skip(self):
        """rate_limited 외 API 에러 → warning 로그 + 스킵 (D-11)"""
        from notion_client import APIResponseError
        from notion_client_mod import save_paper

        paper = make_paper()

        mock_client = MagicMock()
        mock_client.data_sources.query.return_value = {"results": [], "has_more": False}

        api_error = APIResponseError("internal_server_error", 500, "Server error", MagicMock(), "")
        mock_client.pages.create.side_effect = api_error

        with patch("notion_client_mod.get_notion_client", return_value=mock_client):
            with patch("notion_client_mod.logging") as mock_log:
                result = save_paper(paper, "db-id-123", "ds-id-456")

        assert result is False
        mock_log.warning.assert_called()
