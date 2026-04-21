"""RSC (Royal Society of Chemistry) 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


# 비논문 섹션 키워드 (lowercase substring 매칭)
# 각 <a.ItemTitleLink>의 선행 <.GroupHeading> 텍스트를 확인하여 이 키워드가 포함되면 스킵
_SKIP_SECTION_KEYWORDS = (
    "comment",          # Comments / Comment
    "correspondence",   # Correspondence / Reply
    "correction",       # Corrections / Correction
    "corrigend",        # Corrigenda / Corrigendum
    "errat",            # Errata / Erratum
    "retract",          # Retractions / Retraction
)


class RSCParser(BaseParser):
    """RSC 메일 파서.

    RSC 메일 HTML 구조:
    - 섹션 구분: .GroupHeading (Review Articles, Papers, Comments, Corrections, ...)
    - 논문 제목: .ItemTitleLink (<a> 태그)
    - 발신자: *@rsc.org (저널별 다름, 예: MaterialsA@rsc.org)

    비논문 섹션(Comment, Correspondence, Correction 등) 필터링:
    - 각 ItemTitleLink의 선행 GroupHeading을 find_previous로 찾아
      섹션명에 _SKIP_SECTION_KEYWORDS가 포함되면 해당 논문 스킵.
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
                # 현재 링크가 속한 섹션(선행 GroupHeading) 확인
                heading = a_tag.find_previous(class_="GroupHeading")
                if heading is not None:
                    section_name = self._clean_text(heading).lower()
                    if any(kw in section_name for kw in _SKIP_SECTION_KEYWORDS):
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
            logger.warning("RSC 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
