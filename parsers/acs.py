"""ACS Publications 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# 비논문 제목 필터 (대소문자 무시)
_SKIP_TITLES = {"issue information", "front cover", "back cover", "masthead", "table of contents"}


class ACSParser(BaseParser):
    """ACS Publications ASAP 메일 파서.

    ACS 메일 HTML 구조:
    - 논문 제목: <h5> 내부 <a> 태그 텍스트
    - 발신자: updates@acspubs.org
    """

    publisher_name = "ACS Publications"

    def can_parse(self, sender: str, subject: str) -> bool:
        return sender == "updates@acspubs.org"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            for block in soup.select("table.tolkien-column-9"):
                title_tag = block.select_one("h5 a")
                if not title_tag:
                    continue
                title = self._clean_text(title_tag)
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
            logger.warning("ACS 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
