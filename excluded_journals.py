"""서빙/수집에서 완전히 제외할 저널 목록 로더.

Notion DB(진실의 원천)는 절대 건드리지 않고, 서빙 레이어(CSV)와 신규 수집 단계에서만
excluded_journals.json에 등록된 저널을 걸러내기 위한 공용 유틸.

저널 추가/제거는 excluded_journals.json 리스트만 편집하면 된다.
"""
import json
import os
from functools import lru_cache

_EXCLUDED_JOURNALS_PATH = os.path.join(
    os.path.dirname(__file__), "excluded_journals.json"
)


@lru_cache(maxsize=None)
def load_excluded_journals(path: str = None) -> frozenset[str]:
    """excluded_journals.json을 로드하여 정규화(strip + casefold)된 저널명 집합 반환.

    Args:
        path: excluded_journals.json 경로. None이면 이 모듈 디렉토리의 파일 사용.

    Returns:
        정규화된(strip + casefold) 저널명의 frozenset. 파일이 없거나 형식이 잘못되면 빈 set.
    """
    path = path or _EXCLUDED_JOURNALS_PATH
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return frozenset()

    if not isinstance(data, list):
        return frozenset()

    return frozenset(
        j.strip().casefold()
        for j in data
        if isinstance(j, str) and j.strip()
    )


def is_excluded_journal(journal: str, path: str = None) -> bool:
    """저널명이 제외목록에 있는지 확인 (strip + 대소문자 무관 비교).

    Args:
        journal: 확인할 저널명 (원본 표기 그대로 전달 가능, 내부에서 정규화)
        path: excluded_journals.json 경로. None이면 기본 경로 사용.

    Returns:
        제외목록에 있으면 True.
    """
    if not journal:
        return False
    return journal.strip().casefold() in load_excluded_journals(path)
