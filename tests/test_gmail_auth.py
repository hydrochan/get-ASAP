"""Gmail OAuth 인증 모듈 단위 테스트"""
import pytest
from unittest.mock import MagicMock, patch, mock_open


class TestGetGmailService:
    """get_gmail_service() 함수 테스트"""

    def test_get_gmail_service_with_valid_token(self, tmp_path):
        """유효한 token.json이 있으면 Gmail 서비스 객체 반환"""
        token_file = tmp_path / "token.json"
        token_file.write_text('{"token": "valid"}')

        # 유효한 Credentials mock 설정
        mock_creds = MagicMock()
        mock_creds.valid = True

        with patch("auth.GMAIL_TOKEN_PATH", str(token_file)), \
             patch("auth.Credentials.from_authorized_user_file", return_value=mock_creds), \
             patch("auth.build") as mock_build:

            mock_service = MagicMock()
            mock_build.return_value = mock_service

            from auth import get_gmail_service
            result = get_gmail_service()

            # build()가 호출되었는지 확인
            mock_build.assert_called_once_with("gmail", "v1", credentials=mock_creds)
            assert result == mock_service

    def test_get_gmail_service_refreshes_expired_token(self, tmp_path):
        """만료된 토큰이면 creds.refresh() 호출 후 token.json 재저장"""
        token_file = tmp_path / "token.json"
        token_file.write_text('{"token": "expired"}')

        # 만료된 Credentials mock 설정
        mock_creds = MagicMock()
        mock_creds.valid = False
        mock_creds.expired = True
        mock_creds.refresh_token = "refresh_token_value"
        mock_creds.to_json.return_value = '{"token": "refreshed"}'

        with patch("auth.GMAIL_TOKEN_PATH", str(token_file)), \
             patch("auth.Credentials.from_authorized_user_file", return_value=mock_creds), \
             patch("auth.Request") as mock_request, \
             patch("auth.build") as mock_build:

            from auth import get_gmail_service
            get_gmail_service()

            # creds.refresh() 호출 확인
            mock_creds.refresh.assert_called_once()
            # token 파일 재저장 확인 (to_json 호출)
            mock_creds.to_json.assert_called_once()

    def test_get_gmail_service_no_token_triggers_flow(self, tmp_path):
        """token.json 없으면 InstalledAppFlow 실행"""
        non_existent_token = str(tmp_path / "nonexistent_token.json")
        credentials_file = str(tmp_path / "credentials.json")

        with patch("auth.GMAIL_TOKEN_PATH", non_existent_token), \
             patch("auth.GMAIL_CREDENTIALS_PATH", credentials_file), \
             patch("auth.InstalledAppFlow") as mock_flow_class, \
             patch("auth.build"):

            mock_flow = MagicMock()
            mock_flow_class.from_client_secrets_file.return_value = mock_flow
            mock_creds = MagicMock()
            mock_creds.to_json.return_value = "{}"
            mock_flow.run_local_server.return_value = mock_creds

            from auth import get_gmail_service
            get_gmail_service()

            # InstalledAppFlow.from_client_secrets_file() 호출 확인
            mock_flow_class.from_client_secrets_file.assert_called_once()

    def test_auth_uses_config_paths(self):
        """auth.py가 config.GMAIL_CREDENTIALS_PATH와 GMAIL_TOKEN_PATH 사용 확인"""
        import auth
        import config

        # auth.py가 config에서 경로를 import하는지 확인
        assert hasattr(auth, "GMAIL_TOKEN_PATH")
        assert hasattr(auth, "GMAIL_CREDENTIALS_PATH")
        assert hasattr(auth, "GMAIL_SCOPES")
