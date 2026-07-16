"""Cell Press 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)

# Cell Press 메일 footer 링크 텍스트 추가 필터
_CELL_EXTRA_SKIP = {
    "terms and conditions", "privacy policy",
    "manage your preferences", "unsubscribe",
}


class CellPressParser(BaseParser):
    """Cell Press 메일 파서.

    Cell Press 메일 HTML 구조:
    - 논문 제목: <tr> > <td> > <a> 태그 텍스트 (20자 이상)
    - 링크: click.notification.elsevier.com -> cell.com
    - 발신자: cellpress@notification.elsevier.com
    """

    publisher_name = "Cell Press"

    def can_parse(self, sender: str, subject: str) -> bool:
        return "cellpress@" in sender.lower()

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()

            # Cell Press 메일: <tr> > <td> > <a href="...cell.com..."> 패턴
            for td in soup.find_all("td"):
                a_tag = td.find("a", recursive=False)
                if not a_tag:
                    continue
                href = a_tag.get("href", "")
                # cell.com 논문 링크만 필터 (footer 링크 제외)
                if "cell.com" not in href:
                    continue
                title = self._clean_text(a_tag)
                if len(title) < 20:
                    continue
                if title.lower() in _CELL_EXTRA_SKIP:
                    continue
                if not is_valid_paper_title(title):
                    continue
                if title in seen_titles:
                    continue
                # footer 클래스 제외
                td_class = " ".join(td.get("class", []))
                if "footer" in td_class:
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
            logger.warning("Cell Press 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
