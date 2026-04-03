"""환경변수 로드 및 프로젝트 설정 관리"""
import os
from dotenv import load_dotenv

load_dotenv()

# Gmail OAuth 설정 (per D-08)
GMAIL_CREDENTIALS_PATH = os.getenv("GMAIL_CREDENTIALS_PATH", "credentials.json")
GMAIL_TOKEN_PATH = os.getenv("GMAIL_TOKEN_PATH", "token.json")

# Gmail API 스코프 (per D-05: gmail.readonly로 시작)
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Notion 설정
NOTION_TOKEN = os.getenv("NOTION_TOKEN")
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

# 실행 설정
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "6"))
