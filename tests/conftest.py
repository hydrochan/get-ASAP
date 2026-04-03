"""공유 테스트 픽스처"""
import pytest
import os


@pytest.fixture
def mock_env(monkeypatch):
    """테스트용 환경변수 설정"""
    monkeypatch.setenv("GMAIL_CREDENTIALS_PATH", "test_credentials.json")
    monkeypatch.setenv("GMAIL_TOKEN_PATH", "test_token.json")
    monkeypatch.setenv("NOTION_TOKEN", "test_notion_token")
    monkeypatch.setenv("NOTION_DATABASE_ID", "test_db_id")
