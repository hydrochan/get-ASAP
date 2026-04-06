"""Elsevier 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

_SKIP_TITLES = {"issue information", "front cover", "back cover", "masthead", "table of contents"}


class ElsevierParser(BaseParser):
    """Elsevier (ScienceDirect) ASAP 메일 파서."""

    publisher_name = "Elsevier"

    def can_parse(self, sender: str, subject: str) -> bool:
        return sender == "sciencedirect@notification.elsevier.com"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            for h2 in soup.select("h2"):
                a_tag = h2.select_one("a")
                if not a_tag:
                    continue

                title = self._clean_text(a_tag)
                if not title or len(title) < 10:
                    continue
                if title.lower() in _SKIP_TITLES:
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                url = a_tag.get("href", "")
                papers.append(PaperMetadata(
                    title=title,
                    journal="",
                    date="",
                    url=url,
                ))

            return papers

        except Exception as e:
            logger.warning("Elsevier 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
