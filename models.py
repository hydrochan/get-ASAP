"""논문 메타데이터 데이터 모델 (per D-08)"""
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PaperMetadata:
    """Gmail ASAP 메일에서 추출한 논문 메타데이터.

    필수 필드: title, doi, journal, date
    선택 필드: authors (저자 목록), url (논문 링크)
    """
    title: str          # 논문 제목
    doi: str            # DOI 식별자 (중복 방지 기준, per D-09)
    journal: str        # 저널명
    date: str           # 발행일 (ISO 형식 권장: YYYY-MM-DD)
    authors: Optional[list[str]] = field(default=None)  # 저자 목록 (선택)
    url: Optional[str] = None                            # 논문 URL (선택)
