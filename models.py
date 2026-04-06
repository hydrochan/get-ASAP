"""논문 메타데이터 데이터 모델 (per D-08)"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaperMetadata:
    """Gmail ASAP 메일에서 추출한 논문 메타데이터.

    필수 필드: title, journal, date
    """
    title: str          # 논문 제목
    journal: str        # 저널명
    date: str           # 발행일 (ISO 형식 권장: YYYY-MM-DD)
    url: str = ""       # 논문 링크 (메일 내 하이퍼링크)
