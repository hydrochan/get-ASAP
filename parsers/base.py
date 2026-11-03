"""출판사 파서 기저 클래스 (per D-07, D-08)"""
import re
from abc import ABC, abstractmethod

from bs4.element import NavigableString, PreformattedString, Script, Stylesheet, TemplateString, Tag

from models import PaperMetadata

# ---------- 제목 텍스트 추출 (title_text) ----------
# get_text(separator=" ")는 인라인 태그 경계마다 공백을 넣어 CO<sub>2</sub> 를 "CO 2", <i>n</i>-Butyl 을 "n -Butyl" 로 쪼갠다.
# title_text 는 DOM 을 직접 걸으며 블록 태그에만 공백을 넣고, 위·아래첨자·이탤릭 경계의 공백은 규칙으로 정리한다.
_BLOCK_TAGS = frozenset({
    "br", "hr", "p", "div", "li", "ul", "ol", "dl", "dt", "dd", "table", "tbody", "thead", "tfoot", "tr", "td", "th",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "section", "article", "header", "footer", "center", "pre",
})
_SKIP_TAGS = frozenset({"script", "style"})
_SKIP_STRINGS = (PreformattedString, Script, Stylesheet, TemplateString)  # 주석·CDATA·처리지시·script/style 본문

# 사설 영역(PUA) 마커: 걷는 동안 sub/sup/이탤릭의 시작·끝 위치를 표시했다가 공백 규칙을 적용한 뒤 지운다.
_SUB_S, _SUB_E, _SUP_S, _SUP_E, _EM_S, _EM_E = "", "", "", "", "", ""
_MARKS = {"sub": (_SUB_S, _SUB_E), "sup": (_SUP_S, _SUP_E), "em": (_EM_S, _EM_E), "i": (_EM_S, _EM_E)}

_EM = "[]*"  # 이탤릭 마커는 아래 규칙에서 투명하게 건너뛴다
_TITLE_RULES = (
    # 1) sub/sup 끝: 안쪽 공백 제거 / 바깥 공백은 뒤가 "-단어"·닫는 문장부호·또 다른 sub/sup 일 때만 제거
    (re.compile(rf"\s+({_EM}[])"), r"\1"),
    (re.compile(rf"([]{_EM})\s+(?={_EM}(?:[-‐‑][\w(\[]|[)\],.;:/]|[]))"), r"\1"),
    # 2) sub 시작: 앞뒤 공백 제거 ("CO\n<sub>2" -> "CO2")
    (re.compile(rf"\s+({_EM})"), r"\1"),
    (re.compile(rf"({_EM})\s+"), r"\1"),
    # 3) sup 시작: 뒤 공백만 제거 (앞 공백은 보존: "by <sup>13</sup>C")
    (re.compile(rf"({_EM})\s+"), r"\1"),
    # 4) 이탤릭 끝 + 공백 + "-단어": 붙인다 ("1<em>H</em>\n-pyrazole" -> "1H-pyrazole")
    (re.compile(r"()\s+(?=[-‐‑][\w(\[])"), r"\1"),
    # 5) sub/sup 끝에 소문자 2자 이상 단어가 바로 붙으면 띄운다 ("Cl<sub>6</sub><em>via</em>" -> "Cl6 via")
    (re.compile(rf"([])(?={_EM}[a-z]{{2}})"), r"\1 "),
)
_MARKER_RE = re.compile("[-]")
_WS_RE = re.compile(r"\s+")


def _walk_title(node, out: list[str]) -> None:
    for child in node.children:
        if isinstance(child, NavigableString):
            if not isinstance(child, _SKIP_STRINGS):
                out.append(str(child))
        elif isinstance(child, Tag):
            name = (child.name or "").rsplit(":", 1)[-1].lower()  # jats:sub, mml:sup 의 접두어 제거
            if name in _SKIP_TAGS:
                continue
            if name in _BLOCK_TAGS:
                out.append(" ")
                _walk_title(child, out)
                out.append(" ")
            elif name in _MARKS:
                start, end = _MARKS[name]
                out.append(start)
                _walk_title(child, out)
                out.append(end)
            else:
                _walk_title(child, out)


def title_text(tag) -> str:
    """제목 태그의 텍스트. 블록 태그(br·div·td 등)에만 공백을 넣고 인라인 태그 경계는 붙인다.

    CO<sub>2</sub> -> "CO2", Zn<sup>2+</sup> -> "Zn2+", 1<em>H</em>-pyrazole -> "1H-pyrazole".
    "by <sup>13</sup>C" 처럼 윗첨자 앞의 공백은 보존한다. get_text(separator=" ") 대신 모든 출판사 파서의 제목 추출에 쓴다.
    """
    if isinstance(tag, NavigableString):
        return _WS_RE.sub(" ", "" if isinstance(tag, _SKIP_STRINGS) else str(tag)).strip()
    parts: list[str] = []
    _walk_title(tag, parts)
    text = "".join(parts)
    for pattern, repl in _TITLE_RULES:
        text = pattern.sub(repl, text)
    return _WS_RE.sub(" ", _MARKER_RE.sub("", text)).strip()


class BaseParser(ABC):
    """출판사별 메일 파서의 추상 기저 클래스.

    parsers/ 디렉토리에 이 클래스를 상속한 파일을 추가하면 자동 등록된다.
    각 구체 파서는 can_parse와 parse 두 메서드를 반드시 구현해야 한다.
    """

    publisher_name: str = ""  # 출판사 식별 이름 (서브클래스에서 오버라이드)

    @abstractmethod
    def can_parse(self, sender: str, subject: str) -> bool:
        """이 파서가 해당 메일을 처리할 수 있는지 판단.

        Args:
            sender: 메일 발신자 이메일 주소
            subject: 메일 제목

        Returns:
            처리 가능하면 True, 아니면 False
        """

    @abstractmethod
    def parse(self, message_body: str) -> list[PaperMetadata]:
        """메일 본문에서 논문 메타데이터 목록 추출.

        Args:
            message_body: 메일 본문 텍스트 (HTML 또는 plain text)

        Returns:
            추출된 PaperMetadata 목록 (논문이 없으면 빈 리스트)
        """
