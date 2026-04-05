"""Wiley 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# "(저널명 이슈/연도)" 접미사 제거 패턴
_JOURNAL_SUFFIX_RE = re.compile(r'\s*\([^)]+\d{4}\)\s*$')

# 비논문 제목 필터
_SKIP_TITLES = {"issue information", "front cover", "back cover", "masthead", "table of contents"}


class WileyParser(BaseParser):
    """Wiley Online Library ASAP 메일 파서."""

    publisher_name = "Wiley"

    def can_parse(self, sender: str, subject: str) -> bool:
        return sender == "WileyOnlineLibrary@wiley.com"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            for a_tag in soup.select("a.issue-item__title"):
                h5 = a_tag.select_one("h5")
                raw_title = h5.get_text(separator=" ", strip=True) if h5 else a_tag.get_text(separator=" ", strip=True)

                title = _JOURNAL_SUFFIX_RE.sub("", raw_title).strip()
                title = re.sub(r'\s+', ' ', title).strip()

                if not title or len(title) < 10:
                    continue
                if title.lower() in _SKIP_TITLES:
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                papers.append(PaperMetadata(
                    title=title,
                    journal="",
                    date="",
                ))

            return papers

        except Exception as e:
            logger.warning("Wiley 파싱 실패: %s", e)
            return []
