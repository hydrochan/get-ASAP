"""ACS 파서 단위 테스트 (TDD RED 단계)"""
import os
import pytest
from parsers.acs import ACSParser
from models import PaperMetadata

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE = os.path.join(FIXTURE_DIR, "acs_01.html")


@pytest.fixture
def acs_html():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def parser():
    return ACSParser()


def test_acs_can_parse_correct_sender(parser):
    """올바른 발신자 이메일로 can_parse가 True를 반환해야 한다"""
    # publishers.json의 acs.sender 값
    assert parser.can_parse("updates@acspubs.org", "ACS ASAP Alert") is True


def test_acs_can_parse_wrong_sender(parser):
    """다른 출판사 이메일로 can_parse가 False를 반환해야 한다"""
    assert parser.can_parse("other@domain.com", "JACS ASAP") is False


def test_acs_parse_extracts_papers(parser, acs_html):
    """실제 ACS fixture HTML에서 1개 이상의 PaperMetadata를 추출해야 한다"""
    papers = parser.parse(acs_html)
    assert isinstance(papers, list)
    assert len(papers) > 0


def test_acs_parse_doi_format(parser, acs_html):
    """추출된 모든 DOI가 '10.'으로 시작해야 한다"""
    papers = parser.parse(acs_html)
    assert len(papers) > 0
    for paper in papers:
        assert paper.doi.startswith("10."), f"잘못된 DOI 형식: {paper.doi!r}"


def test_acs_parse_title_not_empty(parser, acs_html):
    """추출된 모든 논문 제목이 빈 문자열이 아니어야 한다"""
    papers = parser.parse(acs_html)
    assert len(papers) > 0
    for paper in papers:
        assert paper.title.strip() != "", "빈 제목이 있음"


def test_acs_parse_no_duplicate_doi(parser, acs_html):
    """반환된 논문 목록에 DOI 중복이 없어야 한다"""
    papers = parser.parse(acs_html)
    dois = [p.doi for p in papers]
    assert len(dois) == len(set(dois)), "DOI 중복 발생"


def test_acs_parse_failure_returns_empty(parser):
    """잘못된 HTML 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("invalid html <garbage>")
    assert result == []


def test_acs_parse_empty_returns_empty(parser):
    """빈 문자열 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("")
    assert result == []
