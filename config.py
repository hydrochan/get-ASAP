"""환경변수 로드 및 프로젝트 설정 관리"""
import os
from dotenv import load_dotenv

load_dotenv()

# Gmail OAuth 설정 (per D-08)
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")

# Gmail API 스코프 (Phase 2: gmail.modify로 확장 - 라벨 부여를 위해, per D-05)
# gmail.modify는 gmail.readonly의 상위 집합 (읽기 + 라벨 부여 모두 가능)
# 주의: 스코프 변경 시 기존 token.json 삭제 후 재인증 필요
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]

# Notion 설정
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# 실행 설정
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "6"))
