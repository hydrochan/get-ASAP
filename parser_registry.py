"""파서 플러그인 자동 디스커버리 (per D-07)"""
import importlib.util
import os
from parsers.base import BaseParser


def load_parsers(parsers_dir: str = None) -> list[BaseParser]:
    """parsers/ 디렉토리의 모든 .py 파일을 로드하여 BaseParser 서브클래스 인스턴스 반환.

    디스커버리 흐름:
    1. parsers_dir 내 .py 파일을 이름순으로 순회
    2. __init__.py, base.py, _ 시작 파일은 건너뜀 (내부 구현 파일)
    3. importlib.util로 각 파일을 동적 모듈로 로드 (임포트 없이 실행)
    4. 로드 후 BaseParser.__subclasses__()로 등록된 모든 서브클래스를 수집

    Args:
        parsers_dir: 스캔할 디렉토리 경로. None이면 이 파일 기준 parsers/ 디렉토리 사용

    Returns:
        발견된 BaseParser 구체 서브클래스 인스턴스 목록
    """
    if parsers_dir is None:
        # 이 파일(parser_registry.py)이 있는 디렉토리 기준으로 parsers/ 탐색
        parsers_dir = os.path.join(os.path.dirname(__file__), "parsers")

    # parsers/ 디렉토리의 .py 파일을 알파벳 순으로 로드
    # 로드된 모듈을 보관하여 GC에 의한 서브클래스 해제 방지
    _loaded_modules = []
    for fname in sorted(os.listdir(parsers_dir)):
        if not fname.endswith(".py"):
            continue
        # 내부 구현 파일 건너뛰기: __init__.py, base.py, _로 시작하는 파일
        if fname.startswith("_") or fname == "base.py":
            continue

        path = os.path.join(parsers_dir, fname)
        # importlib.util로 파일을 동적 모듈로 로드 (Python 표준 라이브러리만 사용)
        spec = importlib.util.spec_from_file_location(fname[:-3], path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # 실행 시 BaseParser 서브클래스가 등록됨
        _loaded_modules.append(mod)  # GC 방지: 모듈 참조 유지

    # 로드된 모든 BaseParser 서브클래스를 인스턴스화하여 반환
    # inspect.isabstract로 추상 메서드가 남은 미완성 클래스 제외
    import inspect
    return [
        cls()
        for cls in BaseParser.__subclasses__()
        if not inspect.isabstract(cls)
    ]
