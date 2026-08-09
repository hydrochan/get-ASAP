"""ACS Publications 출판사 메일 파서"""
import html as html_module
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)

ACS_MARKETING_URL_MARKERS = (
    "acs150_collection",
    "mac_dme_launch",
)

ACS_MARKETING_TITLES = {
    "celebrating the 150th anniversary of the american chemical society",
    "bioinformatics & computational biology",
    "precision interventions & medical engineering",
    "biomedical tools & devices",
}

# "Publication Date (Web): August 07, 2026" 같은 영문 월 이름 -> 2자리 숫자.
# 로케일 의존적인 strptime("%B")을 피하기 위해 명시적으로 매핑한다.
_MONTH_NAME_TO_NUM = {
    "january": "01", "february": "02", "march": "03", "april": "04",
    "may": "05", "june": "06", "july": "07", "august": "08",
    "september": "09", "october": "10", "november": "11", "december": "12",
}
_PUB_DATE_RE = re.compile(
    r"([A-Za-z]+)\s+(\d{1,2}),\s*(\d{4})"
)
_FOOTER_JOURNAL_RE = re.compile(
    r"previously signed up to receive journal alerts from", re.I
)


class ACSParser(BaseParser):
    """ACS Publications ASAP 메일 파서.

    지원 포맷 3종 (parse() 시도 순서):
    1. tolkien: updates@acspubs.org, table.tolkien-column-9 > h5 a
    2. Silverchair(신형, 2026-08~): ealerts@acs.org, <strong>DOI:</strong> 앵커
    3. ealerts(구형): journalalerts@acs.org, strong > a
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

            # 포맷 2: ealerts@acs.org 신형 (Silverchair 템플릿, 2026-08~)
            # ⚠ 이 포맷도 <strong>DOI:</strong> 등 strong 태그를 쓰므로
            #   구형 _parse_ealerts(strong > a)보다 먼저 시도해야 오작동하지 않는다.
            papers = self._parse_silverchair(soup)
            if papers:
                return papers

            # 포맷 3: journalalerts@acs.org 구형 (strong > a 템플릿)
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
            url = title_tag.get("href", "")
            if not self._is_valid_entry(title, url, seen_titles):
                continue
            seen_titles.add(title)
            papers.append(PaperMetadata(title=title, journal="", date="", url=url))

        return papers

    def _parse_silverchair(self, soup) -> list[PaperMetadata]:
        """ealerts@acs.org 신형 포맷 (Silverchair 템플릿, 2026-08~).

        기사 블록마다 <strong>DOI:</strong> 행을 앵커로 삼아, 같은 tbody의
        첫 행을 제목으로 취한다. 저널명은 제목(Subject)에 없고 본문
        푸터/배너에만 있으므로 이메일당 1회 추출해 모든 기사에 공용 적용한다.
        """
        papers = []
        seen_titles = set()
        journal = self._extract_silverchair_journal(soup)

        for strong in soup.find_all("strong"):
            if not strong.get_text(strip=True).startswith("DOI"):
                continue

            block = self._silverchair_block(strong)
            if block is None:
                continue
            title_td, doi_td = block

            title = self._clean_text_no_sep(title_td)
            doi_a = doi_td.find("a")
            doi = self._clean_text_no_sep(doi_a) if doi_a else ""
            url = f"https://doi.org/{doi}" if doi else ""

            if not self._is_valid_entry(title, url, seen_titles):
                continue
            seen_titles.add(title)

            date = self._extract_silverchair_date(strong)
            papers.append(PaperMetadata(title=title, journal=journal, date=date, url=url))

        return papers

    @staticmethod
    def _silverchair_block(doi_strong):
        """DOI: strong 태그에서 (제목 td, DOI td) 튜플을 찾는다. 실패 시 None."""
        doi_td = doi_strong.find_parent("td")
        tr = doi_td.find_parent("tr") if doi_td is not None else None
        tbody = tr.find_parent("tbody") if tr is not None else None
        if tbody is None:
            return None
        rows = tbody.find_all("tr", recursive=False)
        if not rows:
            return None
        title_tds = rows[0].find_all("td", recursive=False)
        if not title_tds:
            return None
        return title_tds[0], doi_td

    @staticmethod
    def _extract_silverchair_date(doi_strong) -> str:
        """DOI 행과 같은 tbody 안의 'Publication Date (Web): August 07, 2026' 행을 ISO로 변환.

        로케일 의존적인 strptime("%B") 대신 명시적 월 이름 매핑을 쓴다.
        파싱 실패 시 빈 문자열(상위 main.py가 오늘 날짜로 채움).
        """
        tbody = doi_strong.find_parent("tbody")
        if tbody is None:
            return ""
        for strong in tbody.find_all("strong"):
            if not strong.get_text(strip=True).startswith("Publication Date"):
                continue
            td = strong.find_parent("td")
            if td is None:
                continue
            text = td.get_text(" ", strip=True)
            m = _PUB_DATE_RE.search(text)
            if not m:
                return ""
            month_name, day, year = m.groups()
            month = _MONTH_NAME_TO_NUM.get(month_name.strip().lower())
            if not month:
                return ""
            return f"{year}-{month}-{int(day):02d}"
        return ""

    @staticmethod
    def _extract_silverchair_journal(soup) -> str:
        """이메일 전체에서 저널명을 1회 추출: 푸터 문구 우선, 배너 로고 alt 폴백."""
        node = soup.find(string=_FOOTER_JOURNAL_RE)
        if node is not None:
            em = node.find_next("em")
            if em is not None:
                name = ACSParser._clean_text_no_sep(em)
                if name:
                    return html_module.unescape(name)

        for img in soup.find_all("img", alt=True):
            src = img.get("src", "")
            if "logos/2x/left/white" in src:
                alt = (img.get("alt") or "").strip()
                if alt:
                    return html_module.unescape(re.sub(r"\s+", " ", alt).strip())

        return ""

    def _parse_ealerts(self, soup) -> list[PaperMetadata]:
        """journalalerts@acs.org 포맷: strong > a (제목)"""
        papers = []
        seen_titles = set()

        for strong in soup.find_all("strong"):
            a_tag = strong.find("a")
            if not a_tag:
                continue
            title = self._clean_text(a_tag)
            url = a_tag.get("href", "")
            if not self._is_valid_entry(title, url, seen_titles):
                continue
            seen_titles.add(title)
            papers.append(PaperMetadata(title=title, journal="", date="", url=url))

        return papers

    def _is_valid_entry(self, title: str, url: str, seen: set) -> bool:
        if not is_valid_paper_title(title):
            return False
        if title in seen:
            return False
        if self._is_marketing_entry(title, url):
            return False
        return True

    @staticmethod
    def _is_marketing_entry(title: str, url: str) -> bool:
        title_key = title.strip().lower()
        url_key = (url or "").lower()
        if title_key in ACS_MARKETING_TITLES:
            return True
        return any(marker in url_key for marker in ACS_MARKETING_URL_MARKERS)

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()

    @staticmethod
    def _clean_text_no_sep(tag) -> str:
        """separator 없이 텍스트를 뽑고 공백만 정규화 (위/아래첨자 붙여쓰기 보존용).

        예: 'C(sp<sup>3</sup>)-H' -> 'C(sp3)-H'.
        기존 _clean_text(separator=" ")를 쓰면 'C(sp 3 )-H'로 깨지므로 신형 포맷
        (Silverchair 템플릿) 제목 전용으로 별도 둔다 — 구형 포맷은 절대 이걸 쓰지 않는다.
        """
        text = tag.get_text(strip=False)
        return re.sub(r'\s+', ' ', text).strip()
