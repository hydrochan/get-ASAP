"""Elsevier 파서 단위 테스트"""
import os
import pytest
from parsers.elsevier import ElsevierParser
from models import PaperMetadata

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE = os.path.join(FIXTURE_DIR, "elsevier_01.html")


@pytest.fixture
def elsevier_html():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def parser():
    return ElsevierParser()


def test_elsevier_can_parse_correct_sender(parser):
    """올바른 발신자 이메일로 can_parse가 True를 반환해야 한다"""
    # publishers.json의 elsevier.sender 값
    assert parser.can_parse("sciencedirect@notification.elsevier.com", "Elsevier Alert") is True


def test_elsevier_can_parse_wrong_sender(parser):
    """다른 출판사 이메일로 can_parse가 False를 반환해야 한다"""
    assert parser.can_parse("other@domain.com", "Elsevier") is False


def test_elsevier_parse_extracts_papers(parser, elsevier_html):
    """실제 Elsevier fixture HTML에서 1개 이상의 PaperMetadata를 추출해야 한다."""
    papers = parser.parse(elsevier_html)
    assert isinstance(papers, list)
    assert len(papers) > 0


def test_elsevier_parse_title_not_empty(parser, elsevier_html):
    """추출된 모든 논문 제목이 빈 문자열이 아니어야 한다"""
    papers = parser.parse(elsevier_html)
    assert len(papers) > 0
    for paper in papers:
        assert paper.title.strip() != "", "빈 제목이 있음"


def test_elsevier_parse_no_duplicate_doi(parser, elsevier_html):
    """추출된 논문들 사이에 제목 중복이 없어야 한다 (CrossRef 제거 후 제목 기반 중복 체크)"""
    papers = parser.parse(elsevier_html)
    titles = [p.title for p in papers]
    assert len(titles) == len(set(titles)), "제목 중복 발생"


def test_elsevier_parse_failure_returns_empty(parser):
    """잘못된 HTML 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("bad html <garbage>")
    assert result == []


def test_elsevier_parse_empty_returns_empty(parser):
    """빈 문자열 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("")
    assert result == []
