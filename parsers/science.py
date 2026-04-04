"""Science/Science Advances 출판사 메일 파서 (per D-04, D-05, D-06, D-08)"""
import logging
import re
from bs4 import BeautifulSoup
import crossref_client
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# DOI 패턴 (직접 추출용, Science HTML에는 없지만 방어 코드)
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')

# Science 메일에서 콘텐츠 섹션을 나타내는 스타일 패턴
# em_f24 클래스 td 내부의 a 링크에 논문 제목이 있음
_TITLE_SELECTOR = "td.em_f24 a"


class ScienceParser(BaseParser):
    """Science / Science Advances ASAP 메일 파서.

    Science 메일 HTML 구조:
    - 논문 제목: em_f24 클래스를 가진 <td> 내부 <a> 태그 텍스트
    - DOI: HTML에 노출되지 않음 (암호화된 쿼리스트링만 존재)
    - DOI 조회 전략: CrossRef API로 제목 → DOI 조회
    - 발신자: announcements@aaas.sciencepubs.org (publishers.json 기준)
    """

    publisher_name = "Science"

    def can_parse(self, sender: str, subject: str) -> bool:
        """Science 발신자 이메일로 파싱 가능 여부 판단"""
        return sender == "announcements@aaas.sciencepubs.org"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        """Science 메일 본문에서 논문 메타데이터 목록 추출.

        제목 추출:
        - em_f24 클래스를 가진 td 태그 내부의 a 링크 텍스트

        DOI 추출 전략:
        1. HTML에서 직접 DOI 탐색 (암호화된 URL로 불가)
        2. CrossRef API에 제목으로 조회
        """
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()  # 제목 기반 중복 방지 (DOI 없을 경우 대비)
            seen_dois = set()    # DOI 기반 중복 방지 (per D-09)

            # Science 메일 구조: em_f24 클래스 td 내부 a 태그에 논문 제목
            for a_tag in soup.select(_TITLE_SELECTOR):
                title = self._clean_text(a_tag)
                if not title or len(title) < 10:
                    # 너무 짧은 텍스트는 논문 제목이 아님 (버튼 텍스트 등 필터링)
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                # DOI를 href에서 직접 추출 시도 (Science는 암호화 URL이라 불가)
                href = a_tag.get("href", "")
                doi = ""
                m = DOI_RE.search(href)
                if m:
                    doi = m.group().rstrip(".,;)")

                # DOI가 없으면 CrossRef API로 제목 조회
                if not doi:
                    doi = crossref_client.lookup_doi(title, doi_prefix="10.1126/")

                # DOI 중복 체크
                if doi:
                    if doi in seen_dois:
                        continue
                    seen_dois.add(doi)

                papers.append(PaperMetadata(
                    title=title,
                    doi=doi,
                    journal="",   # 메일에서 저널명 파싱 미구현
                    date="",      # 메일에서 날짜 추출 미구현
                ))

            return papers

        except Exception as e:
            logger.warning("Science 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        """태그 텍스트에서 불필요한 공백 제거"""
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
