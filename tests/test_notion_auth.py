"""Notion 인증 모듈 테스트 (AUTH-02)"""
import pytest
from unittest.mock import MagicMock, patch


def test_get_notion_client_success(mock_env):
    """NOTION_TOKEN이 설정되면 Client 객체를 반환한다"""
    import importlib
    import config
    importlib.reload(config)
    import notion_auth
    importlib.reload(notion_auth)
    with patch("notion_auth.Client") as mock_client:
        mock_client.return_value = MagicMock()
        client = notion_auth.get_notion_client()
        mock_client.assert_called_once()


def test_get_notion_client_no_token_raises(monkeypatch):
    """NOTION_TOKEN이 없으면 ValueError를 발생시킨다"""
    import config
    import notion_auth
    # config.NOTION_TOKEN을 직접 패치 (load_dotenv가 .env에서 재로드하는 문제 우회)
    monkeypatch.setattr(config, "NOTION_TOKEN", None)
    with pytest.raises(ValueError, match="NOTION_TOKEN"):
        notion_auth.get_notion_client()


def test_verify_notion_connection_success():
    """users.me() 성공 시 워크스페이스 이름을 반환한다"""
    mock_client = MagicMock()
    mock_client.users.me.return_value = {
        "type": "bot",
        "bot": {"workspace_name": "Test Workspace"}
    }
    from notion_auth import verify_notion_connection
    result = verify_notion_connection(mock_client)
    assert result == "Test Workspace"


def test_verify_notion_connection_failure():
    """API 오류 시 ConnectionError를 발생시킨다"""
    from notion_client import APIResponseError
    mock_client = MagicMock()
    # APIResponseError 생성자: code, status, message, headers, raw_body_text
    mock_client.users.me.side_effect = APIResponseError(
        "unauthorized", 401, "Unauthorized", MagicMock(), ""
    )
    from notion_auth import verify_notion_connection
    with pytest.raises(ConnectionError, match="Notion API 연결 실패"):
        verify_notion_connection(mock_client)


def test_no_hardcoded_token():
    """notion_auth.py에 토큰이 하드코딩되지 않았는지 확인"""
    import pathlib
    source = pathlib.Path("notion_auth.py").read_text(encoding="utf-8")
    assert "ntn_" not in source, "Notion 토큰이 하드코딩됨"
    assert "secret_" not in source, "비밀 토큰이 하드코딩됨"
