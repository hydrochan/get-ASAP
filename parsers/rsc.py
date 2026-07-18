"""RSC (Royal Society of Chemistry) 출판사 메일 파서"""
import logging
import re
from bs4 import BeautifulSoup
from models import PaperMetadata
from parsers.base import BaseParser
from parsers.filters import is_valid_paper_title

logger = logging.getLogger(__name__)


# 비논문 섹션 키워드 (lowercase substring 매칭)
# 신/구 양식 공용: 섹션 헤딩(구 양식 .GroupHeading / 신 양식 섹션 td) 텍스트에
# 이 키워드가 포함되면 해당 섹션의 항목은 스킵한다.
_SKIP_SECTION_KEYWORDS = (
    "comment",          # Comments / Comment
    "correspondence",   # Correspondence / Reply
    "correction",       # Corrections / Correction
    "corrigend",        # Corrigenda / Corrigendum
    "errat",            # Errata / Erratum
    "retract",          # Retractions / Retraction
    "editorial",        # Editorials (예: "Outstanding Reviewers ..." 안내문 등 비논문 항목)
)

# ── 신 양식(2026-07~) 식별용 정규식 ─────────────────────────────────────────
# 신 양식 메일은 클래스명 없이 인라인 style만으로 구성되어 구 양식의
# .ItemTitleLink / .GroupHeading 클래스가 존재하지 않는다. 대신 각 역할(섹션
# 헤딩/논문 제목/인용·저자 줄)마다 고유한 style 토큰 조합을 갖고 있어 이를
# 정규식으로 매칭해 식별한다.
_NEW_SECTION_STYLE_RE = re.compile(r"font-size:\s*18px;line-height:\s*32px")
_NEW_TITLE_STYLE_RE = re.compile(r"font-size:\s*22px;line-height:\s*1\.4em")
_NEW_CITATION_STYLE_RE = re.compile(r"font-size:\s*17px;line-height:\s*20px;color:\s*#00436d")


class RSCParser(BaseParser):
    """RSC 메일 파서.

    2026-07부터 RSC가 메일 본문 HTML 템플릿을 교체했다. 구 주소로는 여전히
    구 양식 메일이 잔여로 들어오므로 두 양식을 모두 지원한다.

    - 구 양식: 섹션 구분 .GroupHeading, 논문 제목 .ItemTitleLink(<a> 태그).
    - 신 양식(2026-07~): 클래스명이 전혀 없고 인라인 style만 사용. 위 상수
      _NEW_*_STYLE_RE 로 섹션 헤딩/제목/인용 줄을 각각 식별한다.
      (자세한 구조는 _parse_new docstring 참고)

    두 양식 모두 비논문 섹션(Comment, Correspondence, Correction, Editorial 등)은
    _SKIP_SECTION_KEYWORDS로 필터링한다.
    """

    publisher_name = "RSC"

    def can_parse(self, sender: str, subject: str) -> bool:
        return "rsc.org" in sender.lower()

    def parse(self, message_body: str) -> list[PaperMetadata]:
        if not message_body or not message_body.strip():
            return []
        try:
            soup = BeautifulSoup(message_body, "lxml")

            # 신/구 양식 판별: 구 양식에만 존재하는 .ItemTitleLink 클래스 유무로 분기
            if soup.select_one("a.ItemTitleLink"):
                return self._parse_old(soup)
            return self._parse_new(soup)

        except Exception as e:
            logger.warning("RSC 파싱 실패: %s", e)
            return []

    def _parse_old(self, soup) -> list[PaperMetadata]:
        """구 양식: .ItemTitleLink(논문 제목) + .GroupHeading(섹션) 클래스 기반.

        비논문 섹션(Comment, Correspondence, Correction 등) 필터링:
        각 ItemTitleLink의 선행 GroupHeading을 find_previous로 찾아
        섹션명에 _SKIP_SECTION_KEYWORDS가 포함되면 해당 논문 스킵.
        """
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

    def _parse_new(self, soup) -> list[PaperMetadata]:
        """신 양식(2026-07~): 클래스명 없이 인라인 style만 사용하는 템플릿.

        문서 내 <td style="...">를 등장 순서대로 한 번만 순회하며 상태 머신으로
        처리한다 (find_previous/find_next 트리 탐색보다 단순하고 안전함):

        - 섹션 헤딩 style(_NEW_SECTION_STYLE_RE)을 만나면 current_section 갱신,
          진행 중이던 pending 후보는 폐기.
        - 제목 style(_NEW_TITLE_STYLE_RE)을 만나면 그 안의 <a>를 pending 후보로
          저장(제목/url). <a>가 없으면 pending=None.
        - 인용/저자 공용 style(_NEW_CITATION_STYLE_RE)을 만나면: 텍스트에 "doi:"가
          없으면 저자 줄(또는 빈 줄)이므로 무시하고 다음으로 넘어간다. "doi:"가
          있으면 인용 줄이므로 pending을 이번 논문 후보로 확정 -> 섹션 키워드
          필터 -> is_valid_paper_title -> 중복 체크를 거쳐 통과하면 append.
          확정 시도 후에는 성공/실패와 무관하게 pending을 소비(None)한다.
        """
        papers = []
        seen_titles = set()
        current_section = ""
        pending = None  # {"title": str, "url": str} | None

        for td in soup.find_all("td", style=True):
            style = td.get("style", "")

            if _NEW_SECTION_STYLE_RE.search(style):
                current_section = self._clean_text(td).lower()
                pending = None
                continue

            if _NEW_TITLE_STYLE_RE.search(style):
                a_tag = td.find("a")
                if a_tag is not None:
                    pending = {
                        "title": self._clean_text(a_tag),
                        "url": a_tag.get("href", ""),
                    }
                else:
                    pending = None
                continue

            if _NEW_CITATION_STYLE_RE.search(style):
                text = self._clean_text(td)
                if "doi:" not in text.lower():
                    continue  # 저자 줄(또는 빈 줄) -> 인용 줄이 아니므로 무시
                if pending is None:
                    continue

                if any(kw in current_section for kw in _SKIP_SECTION_KEYWORDS):
                    pending = None
                    continue

                title = pending["title"]
                if is_valid_paper_title(title) and title not in seen_titles:
                    seen_titles.add(title)
                    # journal은 구 양식과 동일하게 비워둔다. 인용 줄의 약칭
                    # ("J. Mater. Chem. C")을 쓰면 상위 추론(제목 기반 정식
                    # 명칭)으로 쌓인 기존 데이터와 저널명이 갈라진다.
                    papers.append(PaperMetadata(
                        title=title,
                        journal="",
                        date="",
                        url=pending["url"],
                    ))
                pending = None

        return papers

    @staticmethod
    def _clean_text(tag) -> str:
        text = tag.get_text(separator=" ", strip=True)
        return re.sub(r'\s+', ' ', text).strip()
