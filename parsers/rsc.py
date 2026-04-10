"""RSC (Royal Society of Chemistry) 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


class RSCParser(BaseParser):
    """RSC 메일 파서.

    RSC 메일 HTML 구조:
    - 섹션 구분: .GroupHeading (Review Articles, Papers, ...)
    - 논문 제목: .ItemTitleLink (<a> 태그)
    - 발신자: *@rsc.org (저널별 다름, 예: MaterialsA@rsc.org)
    """

    publisher_name = "RSC"

    def can_parse(self, sender: str, subject: str) -> bool:
        return "rsc.org" in sender.lower()

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            for a_tag in soup.select("a.ItemTitleLink"):
                title = self._clean_text(a_tag)
                if not is_valid_paper_title(title):
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
            logger.warning("RSC 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
