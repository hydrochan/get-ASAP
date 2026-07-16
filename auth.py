"""Gmail OAuth 2.0 인증 모듈 (per D-03, D-04, D-05, D-06)"""
import os
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from config import GMAIL_CREDENTIALS_PATH, GMAIL_TOKEN_PATH, GMAIL_SCOPES


def get_gmail_service():
    """Gmail API 서비스 객체 반환 (인증 포함)

    - token.json 존재 시 로드
    - 만료 시 자동 갱신 (per D-06)
    - 최초 실행 시 브라우저 인증 (per D-04)
    """
    creds = None

    # 저장된 토큰 로드
    if os.path.exists(GMAIL_TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(GMAIL_TOKEN_PATH, GMAIL_SCOPES)

    # 유효한 자격증명이 없으면 인증/갱신
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            # 만료된 토큰 자동 갱신 (per D-06)
            creds.refresh(Request())
        else:
            # 최초 실행: 브라우저로 OAuth 인증 (per D-04)
            flow = InstalledAppFlow.from_client_secrets_file(
                GMAIL_CREDENTIALS_PATH, GMAIL_SCOPES
            )
            creds = flow.run_local_server(port=0)

        # 토큰 저장 (다음 실행 시 재사용)
        with open(GMAIL_TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return build("gmail", "v1", credentials=creds)
