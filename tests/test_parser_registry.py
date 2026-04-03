"""BaseParser ABC + parser_registry 자동 디스커버리 테스트 (TDD RED)"""
import os
import sys
import importlib
import pytest


# ─── Test 1: BaseParser 직접 인스턴스화 불가 ──────────────────────────────

def test_base_parser_is_abstract():
    """BaseParser()를 직접 인스턴스화하면 TypeError가 발생해야 한다"""
    from parsers.base import BaseParser
    with pytest.raises(TypeError):
        BaseParser()


# ─── Test 2: can_parse 미구현 시 TypeError ───────────────────────────────

def test_base_parser_requires_can_parse():
    """can_parse를 구현하지 않은 서브클래스는 인스턴스화 시 TypeError가 발생해야 한다"""
    from parsers.base import BaseParser

    class MissingCanParse(BaseParser):
        # can_parse 미구현, parse만 구현
        def parse(self, message_body: str):
            return []

    with pytest.raises(TypeError):
        MissingCanParse()


# ─── Test 3: parse 미구현 시 TypeError ──────────────────────────────────

def test_base_parser_requires_parse():
    """parse를 구현하지 않은 서브클래스는 인스턴스화 시 TypeError가 발생해야 한다"""
    from parsers.base import BaseParser

    class MissingParse(BaseParser):
        # parse 미구현, can_parse만 구현
        def can_parse(self, sender: str, subject: str) -> bool:
            return False

    with pytest.raises(TypeError):
        MissingParse()


# ─── Test 4: 완전 구현 서브클래스 정상 생성 ─────────────────────────────

def test_concrete_parser_instantiation():
    """can_parse와 parse를 모두 구현한 서브클래스는 정상적으로 생성되어야 한다"""
    from parsers.base import BaseParser
    from models import PaperMetadata

    class ConcreteParser(BaseParser):
        publisher_name = "TestPublisher"

        def can_parse(self, sender: str, subject: str) -> bool:
            return sender == "test@example.com"

        def parse(self, message_body: str) -> list[PaperMetadata]:
            return []

    parser = ConcreteParser()
    assert parser is not None
    assert parser.can_parse("test@example.com", "subject") is True
    assert parser.parse("body") == []


# ─── Test 5: parsers/ 디렉토리 자동 디스커버리 ─────────────────────────

def test_auto_discovery(tmp_path):
    """parsers/ 디렉토리에 DummyParser 파일을 추가하면 load_parsers()에 포함되어야 한다"""
    from parsers.base import BaseParser

    # 임시 parsers 디렉토리에 DummyParser 파일 생성
    dummy_file = tmp_path / "dummy_parser.py"
    dummy_file.write_text(
        """
import sys
# 실제 models 경로를 sys.path에 추가 (동적 로드이므로 필요)
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from parsers.base import BaseParser
from models import PaperMetadata

class DummyAutoParser(BaseParser):
    publisher_name = "DummyAuto"

    def can_parse(self, sender: str, subject: str) -> bool:
        return sender == "dummy@auto.com"

    def parse(self, message_body: str) -> list[PaperMetadata]:
        return []
""",
        encoding="utf-8",
    )

    # 로드 전 서브클래스 수 기록 (테스트 간 격리)
    before = set(BaseParser.__subclasses__())

    from parser_registry import load_parsers
    parsers = load_parsers(str(tmp_path))

    # 새로 추가된 서브클래스 확인
    after = set(BaseParser.__subclasses__())
    new_classes = after - before

    assert len(new_classes) >= 1, "DummyAutoParser가 서브클래스로 등록되어야 한다"
    class_names = [cls.__name__ for cls in new_classes]
    assert "DummyAutoParser" in class_names


# ─── Test 6: 무시 대상 파일 확인 ────────────────────────────────────────

def test_discovery_ignores_non_parser(tmp_path):
    """__init__.py, base.py, 언더스코어로 시작하는 파일은 무시해야 한다"""
    from parsers.base import BaseParser

    # 무시되어야 할 파일들 생성
    (tmp_path / "__init__.py").write_text("# init", encoding="utf-8")
    (tmp_path / "base.py").write_text("# base", encoding="utf-8")
    (tmp_path / "_private.py").write_text("# private", encoding="utf-8")

    # 로드 전 서브클래스 수 기록
    before = set(BaseParser.__subclasses__())

    from parser_registry import load_parsers
    parsers = load_parsers(str(tmp_path))

    after = set(BaseParser.__subclasses__())
    new_classes = after - before

    # 무시 대상 파일에서는 새 서브클래스가 등록되지 않아야 한다
    assert len(new_classes) == 0, f"무시 대상 파일에서 파서가 등록되면 안 됨: {new_classes}"
