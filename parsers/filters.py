"""파서 공용 필터 유틸: 비논문 엔트리(저널명, 저자명, 편집자 페이지 등) 제거"""
import json
import os
import re
from functools import lru_cache

# 저자명, 편집 페이지 등 논문이 아닌 공통 타이틀 (정확 매칭)
SKIP_TITLES = {
    "issue information",
    "front cover",
    "back cover",
    "inside cover",
    "inside front cover",
    "inside back cover",
    "outside front cover",
    "outside back cover",
    "masthead",
    "issue editorial masthead",
    "issue publication information",
    "table of contents",
    "contents",
    "editorial board",
    "author index",
    "subject index",
    "contributors",
    "acknowledgments",
    "acknowledgements",
    "corrigendum",
    "erratum",
    "editorial",
}

# 제목이 이 prefix로 시작하면 비논문으로 간주
# (예: "Outside Front Cover: ...", "Correction to ...", "Erratum for ...")
SKIP_PREFIXES = (
    "outside front cover:",
    "outside back cover:",
    "inside front cover:",
    "inside back cover:",
    "front cover:",
    "back cover:",
    "cover picture:",
    "cover feature:",
    "frontispiece:",
    "correction to",
    "correction:",
    "corrigendum to",
    "corrigendum:",
    "erratum to",
    "erratum for",
    "erratum:",
    "retraction of",
    "retraction:",
    "retraction notice",
    "expression of concern",
    "withdrawn:",
    "outstanding reviewers for",
    "preface to",
    "comment on ",
    "comments on ",
    "reply to ",
    "reply to:",
    "rebuttal to ",
    "correspondence on ",
    # 제목 앞에 라벨을 단 정정·부록·서문 공지 (DB 실측: 라벨 접두 제목 31건이 통과하고 있었다)
    # 뒤에 공백/콜론을 붙여 단어 경계를 맞춘다 - "Correctional ...", "Addendums ..." 같은 일반 제목은 걸리지 않는다.
    "correction for ",
    "corrigendum on ",
    "erratum on ",
    "addendum to ",
    "addendum:",
    "author correction:",
    "publisher correction:",
    "retraction note:",
    "preface:",
    "news spotlights",
    "in memoriam",
    "obituary:",
)

# 접두 튜플로 쓰기 어려운 라벨(따옴표·대시 변형, 선택적 관사). 소문자·strip 된 제목에 match 한다.
# 따옴표: 직선(" ') · 곡선(“ ” ‘ ’). 대시: - U+2010/2011 – — ― − (공백 유무 무관).
# 'editorial' 은 통째로 막지 않는다 - "Editorial and peer review dynamics at ..." 는 연구 논문이다.
_Q = "\"'\u201c\u201d\u2018\u2019"
_DASH = "\\-\u2010\u2011\u2013\u2014\u2015\u2212"
SKIP_LABEL_RE = re.compile(
    # 논평·편집자 서신에 대한 답변: "Response to “Comment on ...”", "Response to the Letter to the Editor ..."
    # ("Response to geographic song variation ..." 같은 연구 제목은 comment/letter 가 바로 뒤따르지 않아 통과)
    rf"^(?:response\s+to\s+[{_Q}]?(?:the\s+)?[{_Q}]?(?:comments?|letters?)\b"
    # 편집자 글: "Editorial: ...", "Editorial—Special Issue ...", "Editorial Note: ...", "Editorial for the Special Issue ..."
    rf"|editorial\s*[:{_DASH}]"
    rf"|editorial\s+notes?\b"
    rf"|editorial\s+for\s)"
)

# 제목 중간에 등장해도 비논문으로 간주할 문구(위치 무관, "contains" 매칭)
# 예: "{journal} themed collection on ..."
SKIP_SUBSTRINGS = (
    "themed collection",
)

@lru_cache(maxsize=1)
def _load_journal_names() -> frozenset[str]:
    """publishers.json 의 모든 journal 이름을 소문자로 로드."""
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "publishers.json")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return frozenset()
    names: set[str] = set()
    for pub in data.values():
        for j in pub.get("journals", []):
            names.add(j.strip().lower())
    return frozenset(names)


def has_skip_label(low: str) -> bool:
    """소문자·strip 된 제목이 비논문 라벨(SKIP_PREFIXES 접두 또는 SKIP_LABEL_RE)로 시작하는지."""
    return low.startswith(SKIP_PREFIXES) or SKIP_LABEL_RE.match(low) is not None


def is_valid_paper_title(title: str) -> bool:
    """휴리스틱으로 논문 제목 여부 판정. 논문이 아니면 False."""
    if not title:
        return False
    t = title.strip()
    if len(t) < 10:
        return False

    low = t.lower()
    if low in SKIP_TITLES:
        return False

    # prefix·라벨 매칭 (예: "Outside Front Cover: ...", "Correction to ...", "Editorial—Special Issue ...")
    if has_skip_label(low):
        return False

    # 중간에 끼어 있어도 비논문으로 볼 수 있는 문구 (contains 매칭)
    for substr in SKIP_SUBSTRINGS:
        if substr in low:
            return False

    # 저널명 그 자체(예: "Chemical Engineering Journal")는 제외
    journal_names = _load_journal_names()
    if low in journal_names:
        return False

    # 저널명 + 서브타이틀(콜론 표기) 패턴 제외
    # 예: "Applied Catalysis A: General", "Applied Catalysis B: Environment and Energy"
    # publishers.json에는 약칭("Applied Catalysis A")만 등록되어 있어도 풀네임을 자동 제거
    for j in journal_names:
        if low.startswith(j + ":"):
            return False

    # 단어 수가 너무 적으면 논문 제목으로 보기 어려움
    words = t.split()
    if len(words) < 4:
        return False

    return True
