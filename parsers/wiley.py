"""Wiley 출판사 메일 파서 (per D-04, D-05, D-06, D-08)"""
import logging
import re
from bs4 import BeautifulSoup
import crossref_client
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# DOI 패턴 (직접 추출용, Wiley HTML에는 없지만 방어 코드)
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')

# Wiley 메일에서 저널명/이슈 정보가 제목 뒤에 괄호로 붙는 패턴 제거
# 예: "Some Title (Adv. Energy Mater. 13/2026)" → "Some Title"
_JOURNAL_SUFFIX_RE = re.compile(r'\s*\([^)]+\d{4}\)\s*$')


class WileyParser(BaseParser):
    """Wiley Online Library ASAP 메일 파서.

    Wiley 메일 HTML 구조:
    - 논문 제목: <a class="issue-item__title"> 내부 <h5> 태그 텍스트
    - DOI: HTML에 노출되지 않음 (추적 리다이렉트 URL만 존재)
    - DOI 조회 전략: CrossRef API로 제목 → DOI 조회
    - 발신자: WileyOnlineLibrary@wiley.com (publishers.json 기준)
    """

    publisher_name = "Wiley"

    def can_parse(self, sender: str, subject: str) -> bool:
        """Wiley 발신자 이메일로 파싱 가능 여부 판단"""
        return sender == "WileyOnlineLibrary@wiley.com"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        """Wiley 메일 본문에서 논문 메타데이터 목록 추출.

        제목 추출:
        - issue-item__title 클래스를 가진 <a> 태그 내부 <h5> 텍스트
        - 제목 뒤 "(저널명 이슈/연도)" 패턴 제거

        DOI 추출 전략:
        1. HTML에서 직접 DOI 탐색 (추적 URL이라 불가)
        2. CrossRef API에 제목으로 조회
        """
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_titles = set()  # 제목 기반 중복 방지
            seen_dois = set()    # DOI 기반 중복 방지 (per D-09)

            # Wiley 메일 구조: a.issue-item__title 내 h5 태그에 논문 제목
            for a_tag in soup.select("a.issue-item__title"):
                h5 = a_tag.select_one("h5")
                if not h5:
                    # h5 없으면 a 태그 자체 텍스트 사용
                    raw_title = a_tag.get_text(separator=" ", strip=True)
                else:
                    raw_title = h5.get_text(separator=" ", strip=True)

                # "(저널명 이슈/연도)" 형태의 접미사 제거
                # 예: "Some Title (Adv. Energy Mater. 13/2026)" → "Some Title"
                title = _JOURNAL_SUFFIX_RE.sub("", raw_title).strip()
                title = re.sub(r'\s+', ' ', title).strip()

                if not title or len(title) < 10:
                    continue
                if title in seen_titles:
                    continue
                seen_titles.add(title)

                # DOI를 href에서 직접 추출 시도 (Wiley는 추적 URL이라 불가)
                href = a_tag.get("href", "")
                doi = ""
                m = DOI_RE.search(href)
                if m:
                    doi = m.group().rstrip(".,;)")

                # DOI가 없으면 CrossRef API로 제목 조회
                if not doi:
                    doi = crossref_client.lookup_doi(title)

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
            logger.warning("Wiley 파싱 실패: %s", e)
            return []
