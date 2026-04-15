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
NOTION_DATABASE_ID = os.getenv("NOTION_DATABASE_ID")  # 없으면 NOTION_PARENT_PAGE_ID로 자동 생성
NOTION_PARENT_PAGE_ID = os.getenv("NOTION_PARENT_PAGE_ID")

# 실행 설정
CHECK_INTERVAL_HOURS = int(os.getenv("CHECK_INTERVAL_HOURS", "6"))

# 대시보드 설정
# 단일 사용자 (레거시 호환)
DASHBOARD_USERNAME = os.getenv("DASHBOARD_USERNAME", "")
DASHBOARD_PASSWORD_HASH = os.getenv("DASHBOARD_PASSWORD_HASH", "")

# 다중 사용자 지원: DASHBOARD_USERS JSON 형식 {"username": "bcrypt_hash", ...}
# 예: DASHBOARD_USERS={"alice":"$2b$12$...","bob":"$2b$12$..."}
# 레거시 DASHBOARD_USERNAME/DASHBOARD_PASSWORD_HASH도 아래 dict에 자동 병합
import json
DASHBOARD_USERS: dict[str, str] = {}
_users_json = os.getenv("DASHBOARD_USERS", "").strip()
if _users_json:
    try:
        parsed = json.loads(_users_json)
        if isinstance(parsed, dict):
            DASHBOARD_USERS.update({str(k): str(v) for k, v in parsed.items()})
    except json.JSONDecodeError:
        # 파싱 실패 시 로거 없이 무시 (단일 사용자만 활성화)
        pass
if DASHBOARD_USERNAME and DASHBOARD_PASSWORD_HASH and DASHBOARD_USERNAME not in DASHBOARD_USERS:
    DASHBOARD_USERS[DASHBOARD_USERNAME] = DASHBOARD_PASSWORD_HASH

# 관리자 사용자명 목록 (Stats 탭 접근 권한). 쉼표로 구분.
# 기본값: 레거시 DASHBOARD_USERNAME이 자동 관리자
_admins_raw = os.getenv("DASHBOARD_ADMINS", "").strip()
if _admins_raw:
    DASHBOARD_ADMINS = {u.strip() for u in _admins_raw.split(",") if u.strip()}
elif DASHBOARD_USERNAME:
    DASHBOARD_ADMINS = {DASHBOARD_USERNAME}
else:
    DASHBOARD_ADMINS = set()
