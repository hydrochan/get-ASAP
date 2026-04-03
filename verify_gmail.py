"""Gmail API 연결 검증 스크립트 (per D-09)"""
from auth import get_gmail_service
from googleapiclient.errors import HttpError


def main():
    """Gmail API 연결을 검증한다 - 라벨 목록 조회"""
    try:
        service = get_gmail_service()
        results = service.users().labels().list(userId="me").execute()
        labels = results.get("labels", [])
        print(f"Gmail 연결 성공: {len(labels)}개 라벨 확인")
        for label in labels[:5]:
            print(f"  - {label['name']}")
        if len(labels) > 5:
            print(f"  ... 외 {len(labels) - 5}개")
    except HttpError as error:
        print(f"Gmail API 오류: {error}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
