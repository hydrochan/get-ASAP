"""ACS Publications 출판사 메일 파서 (per D-04, D-05, D-06, D-08)"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser

logger = logging.getLogger(__name__)

# DOI 패턴: "10." 으로 시작하는 표준 DOI 형식
DOI_RE = re.compile(r'10\.\d{4,9}/[^\s"<>#?&]+')


class ACSParser(BaseParser):
    """ACS Publications (American Chemical Society) ASAP 메일 파서.

    ACS 메일 HTML 구조:
    - 논문 제목: <h5> 내부 <a> 태그 텍스트
    - DOI: "DOI: 10.1021/xxx" 텍스트를 포함하는 <a> 태그 텍스트
    - 발신자: updates@acspubs.org (publishers.json 기준)
    """

    publisher_name = "ACS Publications"

    def can_parse(self, sender: str, subject: str) -> bool:
        """ACS 발신자 이메일로 파싱 가능 여부 판단"""
        return sender == "updates@acspubs.org"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        """ACS 메일 본문에서 논문 메타데이터 목록 추출.

        DOI 추출 전략:
        1. "DOI: 10.xxx/yyy" 텍스트가 있는 <a> 태그에서 직접 추출
        2. <img src> 속성에서 pubs.acs.org/cms/10.xxx/... 패턴으로 추출 (폴백)

        제목 추출:
        - DOI 링크 바로 앞에 위치한 <h5> 태그 내 <a> 텍스트
        - 각 논문 블록은 tolkien-column-9 테이블 셀로 구성됨
        """
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")
            papers = []
            seen_dois = set()  # 중복 DOI 방지 (per D-09)

            # ACS 메일 구조: 각 논문 블록 = tolkien-column-9 col-1 테이블
            # 각 블록 내에 제목(h5 a)과 DOI("DOI: 10.xxx" 텍스트 a)가 함께 있음
            article_blocks = soup.select("table.tolkien-column-9")

            for block in article_blocks:
                # 제목 추출: h5 태그 내 a 링크 텍스트
                title_tag = block.select_one("h5 a")
                if not title_tag:
                    continue
                title = self._clean_text(title_tag)
                if not title:
                    continue

                # DOI 추출: "DOI: 10.xxx/yyy" 텍스트를 포함한 a 태그
                doi = ""
                for a_tag in block.select("a"):
                    text = a_tag.get_text(strip=True)
                    if text.startswith("DOI:"):
                        # "DOI: 10.1021/acsanm.5c05445" 형식
                        m = DOI_RE.search(text)
                        if m:
                            doi = m.group().rstrip(".,;)")
                            break

                # DOI를 텍스트에서 못 찾으면 img src에서 추출 (폴백)
                if not doi:
                    for img in block.select("img"):
                        src = img.get("src", "")
                        m = DOI_RE.search(src)
                        if m:
                            # src: "https://pubs.acs.org/cms/10.1021/xxx/asset/..."
                            # DOI는 첫 번째 "/" 이전까지만 유효
                            raw = m.group()
                            # asset 경로 이전까지 잘라냄
                            parts = raw.split("/")
                            if len(parts) >= 2:
                                doi = f"{parts[0]}/{parts[1]}"
                            break

                # DOI가 없으면 스킵 (Phase 4 중복 방지가 DOI 기반)
                if not doi:
                    continue
                if doi in seen_dois:
                    continue
                seen_dois.add(doi)

                papers.append(PaperMetadata(
                    title=title,
                    doi=doi,
                    journal="",   # 메일에 저널명이 명시되지 않아 빈값
                    date="",      # 메일에 날짜가 명시되지 않아 빈값
                ))

            return papers

        except Exception as e:
            logger.warning("ACS 파싱 실패: %s", e)
            return []

    @staticmethod
    def _clean_text(tag) -> str:
        """태그 텍스트에서 불필요한 공백 제거"""
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
