"""파서 공용 필터 유틸: 비논문 엔트리(저널명, 저자명, 편집자 페이지 등) 제거"""
import json
import os
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
    "withdrawn:",
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

    # prefix 매칭 (예: "Outside Front Cover: ...", "Correction to ...")
    for prefix in SKIP_PREFIXES:
        if low.startswith(prefix):
            return False

    # 저널명 그 자체(예: "Chemical Engineering Journal")는 제외
    if low in _load_journal_names():
        return False

    # 단어 수가 너무 적으면 논문 제목으로 보기 어려움
    words = t.split()
    if len(words) < 4:
        return False

    return True
