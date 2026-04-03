"""Notion API 인증 모듈 (per D-07, D-08, D-10)"""
from notion_client import Client, APIResponseError
from config import NOTION_TOKEN


def get_notion_client() -> Client:
    """Notion API 클라이언트 반환

    .env의 NOTION_TOKEN 환경변수를 사용한다 (per D-07).
    토큰이 없으면 ValueError를 발생시킨다.
    """
    if not NOTION_TOKEN:
        raise ValueError(
            "NOTION_TOKEN 환경변수가 설정되지 않았습니다. "
            ".env 파일에 NOTION_TOKEN=your_token 을 추가하세요."
        )
    return Client(auth=NOTION_TOKEN)


def verify_notion_connection(client: Client) -> str:
    """Notion API 연결 검증 - 워크스페이스 이름 반환

    GET /v1/users/me 엔드포인트로 봇 사용자 정보를 조회한다.
    """
    try:
        bot_info = client.users.me()
        workspace_name = bot_info.get("bot", {}).get("workspace_name", "Unknown")
        return workspace_name
    except APIResponseError as e:
        raise ConnectionError(f"Notion API 연결 실패: {e}")
