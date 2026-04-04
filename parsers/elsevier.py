"""Elsevier 출판사 메일 파서 (per D-04, D-05, D-06, D-08)"""
import logging
import re
from bs4 import BeautifulSoup
import crossref_client
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# DOI 패턴 (직접 추출 시 사용, Elsevier에는 드물게 있을 수 있음)
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')


class ElsevierParser(BaseParser):
    """Elsevier (ScienceDirect) ASAP 메일 파서.

    Elsevier 메일 HTML 구조:
    - 논문 제목: <h2> 내부 <a> 태그 텍스트
    - DOI: HTML에 직접 노출되지 않음 (PII key만 존재)
    - DOI 조회 전략: CrossRef API로 제목 → DOI 조회
    - 발신자: sciencedirect@notification.elsevier.com (publishers.json 기준)
    """

    publisher_name = "Elsevier"

    def can_parse(self, sender: str, subject: str) -> bool:
        """Elsevier 발신자 이메일로 파싱 가능 여부 판단"""
        return sender == "sciencedirect@notification.elsevier.com"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        """Elsevier 메일 본문에서 논문 메타데이터 목록 추출.

        제목 추출:
        - <h2> 태그 내부 <a> 링크 텍스트

        DOI 추출 전략:
        1. HTML에서 직접 DOI 탐색 (드물게 존재할 경우 대비)
        2. 없으면 CrossRef API에 제목으로 조회
        """
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_dois = set()  # 중복 DOI 방지 (per D-09)

            # Elsevier 메일 구조: <h2> 태그 내 <a> 링크에 논문 제목
            for h2 in soup.select("h2"):
                a_tag = h2.select_one("a")
                if not a_tag:
                    continue

                title = self._clean_text(a_tag)
                if not title:
                    continue

                # DOI를 href에서 직접 추출 시도 (실패하면 CrossRef 폴백)
                href = a_tag.get("href", "")
                doi = ""
                m = DOI_RE.search(href)
                if m:
                    doi = m.group().rstrip(".,;)")

                # DOI가 없으면 CrossRef API로 제목 조회
                if not doi:
                    doi = crossref_client.lookup_doi(title)

                # DOI 중복 체크 (DOI 있는 경우)
                if doi:
                    if doi in seen_dois:
                        continue
                    seen_dois.add(doi)

                papers.append(PaperMetadata(
                    title=title,
                    doi=doi,
                    journal="",   # 메일에서 저널명 파싱 불필요
                    date="",      # 메일에서 날짜 추출 미구현
                ))

            return papers

        except Exception as e:
            logger.warning("Elsevier 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        """태그 텍스트에서 불필요한 공백 제거"""
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
