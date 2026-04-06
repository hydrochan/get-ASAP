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
        sender_lower = sender.lower()
        return "acspubs.org" in sender_lower or "acs.org" in sender_lower

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")

            # 포맷 1: updates@acspubs.org (tolkien 템플릿)
            papers = self._parse_tolkien(soup)
            if papers:
                return papers

            # 포맷 2: journalalerts@acs.org (strong > a 템플릿)
            return self._parse_ealerts(soup)

        except Exception as e:
            logger.warning("ACS 파싱 실패: %s", e)
            return []

    def _parse_tolkien(self, soup) -> list[PaperMetadata]:
        """updates@acspubs.org 포맷: table.tolkien-column-9 > h5 a"""
        papers = []
        seen_titles = set()

        for block in soup.select("table.tolkien-column-9"):
            title_tag = block.select_one("h5 a")
            if not title_tag:
                continue
            title = self._clean_text(title_tag)
            if not self._is_valid_title(title, seen_titles):
                continue
            seen_titles.add(title)
            url = title_tag.get("href", "")
            papers.append(PaperMetadata(title=title, journal="", date="", url=url))

        return papers

    def _parse_ealerts(self, soup) -> list[PaperMetadata]:
        """journalalerts@acs.org 포맷: strong > a (제목)"""
        papers = []
        seen_titles = set()

        for strong in soup.find_all("strong"):
            a_tag = strong.find("a")
            if not a_tag:
                continue
            title = self._clean_text(a_tag)
            if not self._is_valid_title(title, seen_titles):
                continue
            seen_titles.add(title)
            url = a_tag.get("href", "")
            papers.append(PaperMetadata(title=title, journal="", date="", url=url))

        return papers

    def _is_valid_title(self, title: str, seen: set) -> bool:
        if not title or len(title) < 10:
            return False
        if title.lower() in _SKIP_TITLES:
            return False
        if title in seen:
            return False
        return True

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
