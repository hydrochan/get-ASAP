"""논문 메타데이터 데이터 모델 (per D-08)"""
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import urlsplit

# XSS 방어: URL 스킴 허용목록. http/https/mailto 외(javascript:, data:, vbscript: 등)는 저장 시 차단
_ALLOWED_URL_SCHEMES = {"http", "https", "mailto"}


def _sanitize_url(url: str) -> str:
    """허용목록에 없는 스킴(또는 스킴 파싱 불가/스킴 없는 값)은 빈 문자열로 치환한다."""
    if not url:
        return ""
    try:
        scheme = urlsplit(url.strip()).scheme.lower()
    except ValueError:
        return ""
    return url if scheme in _ALLOWED_URL_SCHEMES else ""


@dataclass
class PaperMetadata:
    """Gmail ASAP 메일에서 추출한 논문 메타데이터.

    필수 필드: title, journal, date
    """
    title: str          # 논문 제목
    journal: str        # 저널명
    date: str           # 발행일 (ISO 형식 권장: YYYY-MM-DD)
    url: str = ""       # 논문 링크 (메일 내 하이퍼링크)

    def __post_init__(self):
        self.url = _sanitize_url(self.url)
