"""Notion API 연결 검증 스크립트 (per D-10)"""
from notion_auth import get_notion_client, verify_notion_connection


def main():
    """Notion API 연결을 검증한다 - 워크스페이스 정보 조회"""
    try:
        client = get_notion_client()
        workspace_name = verify_notion_connection(client)
        print(f"Notion 연결 성공: 워크스페이스 '{workspace_name}'")
    except ValueError as e:
        print(f"설정 오류: {e}")
        raise SystemExit(1)
    except ConnectionError as e:
        print(f"연결 오류: {e}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
