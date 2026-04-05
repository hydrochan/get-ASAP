"""Nature 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

_SKIP_TITLES = {
    "issue information", "front cover", "back cover", "masthead",
    "table of contents", "amendments & corrections",
    "option for transparent peer review",
}


class NatureParser(BaseParser):
    """Nature 메일 파서.

    Nature 메일 HTML 구조:
    - 논문 제목: <td> 안의 <div> 안 <a> 태그 텍스트
    - 링크: springernature.com 트래킹 URL
    - 발신자: ealert@nature.com
    """

    publisher_name = "Nature"

    def can_parse(self, sender: str, subject: str) -> bool:
        return "nature.com" in sender.lower()

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            # Nature 메일: <td> > <div> > <a> 패턴 (springernature.com 링크)
            for td in soup.find_all("td"):
                div = td.find("div", recursive=False)
                if not div:
                    continue
                a_tag = div.find("a", recursive=False)
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

                papers.append(PaperMetadata(
                    title=title,
                    journal="",
                    date="",
                ))

            return papers

        except Exception as e:
            logger.warning("Nature 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
