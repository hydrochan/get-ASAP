"""Nature 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


class NatureParser(BaseParser):
    """Nature 메일 파서.

    Nature 메일 HTML 구조:
    - 섹션 구분: <h2> 태그 (Editorial, News & Views, Articles, ...)
    - 논문 섹션: Articles, Letters, Reviews, Perspectives, Research
    - 논문 제목: <td> > <div> > <a> 또는 <td> > <span> > <a>
    - 발신자: ealert@nature.com, alerts@nature.com
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

            # 논문 섹션의 <h2> 찾기
            # Articles/Letters: 일반 저널, Reviews/Perspectives: 리뷰 저널, Research: 본지
            target_sections = {"articles", "letters", "reviews", "perspectives", "research"}
            article_sections = []
            for h2 in soup.find_all("h2"):
                section_name = h2.get_text(strip=True).lower()
                if section_name in target_sections:
                    article_sections.append(h2)

            if not article_sections:
                logger.debug("Nature 메일에 논문 섹션 없음")
                return []

            # 각 섹션에서 다음 <h2> 전까지의 <a> 태그 추출
            for section_h2 in article_sections:
                current = section_h2
                while True:
                    current = current.find_next()
                    if current is None:
                        break
                    # 다음 h2를 만나면 섹션 끝
                    if current.name == "h2":
                        break
                    # <td> > <div|span> > <a> 패턴 매칭
                    if current.name != "a":
                        continue
                    parent = current.parent
                    if not parent or parent.name not in ("div", "span"):
                        continue
                    gp = parent.parent
                    if not gp or gp.name != "td":
                        continue

                    title = self._clean_text(current)
                    if not is_valid_paper_title(title):
                        continue
                    if title in seen_titles:
                        continue
                    seen_titles.add(title)

                    url = current.get("href", "")
                    papers.append(PaperMetadata(
                        title=title,
                        journal="",
                        date="",
                        url=url,
                    ))

            return papers

        except Exception as e:
            logger.warning("Nature 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
