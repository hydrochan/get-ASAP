"""Science/Science Advances 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)

_TITLE_SELECTOR = "td.em_f24 a"


class ScienceParser(BaseParser):
    """Science / Science Advances ASAP 메일 파서."""

    publisher_name = "Science"

    def can_parse(self, sender: str, subject: str) -> bool:
        return "aaas.sciencepubs.org" in sender.lower()

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            # "Research Article|..." 라벨이 붙은 항목만 추출
            # td.em_txt_grey 에 "Research Article|분야" 텍스트가 있고,
            # 바로 다음 td.em_f24 a 가 논문 제목
            for label_td in soup.select("td.em_txt_grey"):
                label_text = label_td.get_text(strip=True)
                if not label_text.startswith("Research Article"):
                    continue
                # 다음 td.em_f24 안의 a 태그가 논문 제목
                title_td = label_td.find_next("td", class_="em_f24")
                if not title_td:
                    continue
                a_tag = title_td.find("a")
                if not a_tag:
                    continue

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
            logger.warning("Science 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
