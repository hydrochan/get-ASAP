"""Wiley 파서 단위 테스트 (TDD)"""
import os
import pytest
from unittest.mock import patch
from parsers.wiley import WileyParser
from models import PaperMetadata

FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
FIXTURE = os.path.join(FIXTURE_DIR, "wiley_01.html")


@pytest.fixture
def wiley_html():
    with open(FIXTURE, encoding="utf-8") as f:
        return f.read()


@pytest.fixture
def parser():
    return WileyParser()


def test_wiley_can_parse_correct_sender(parser):
    """올바른 발신자 이메일로 can_parse가 True를 반환해야 한다"""
    # publishers.json의 wiley.sender 값
    assert parser.can_parse("WileyOnlineLibrary@wiley.com", "New Articles") is True


def test_wiley_can_parse_wrong_sender(parser):
    """다른 출판사 이메일로 can_parse가 False를 반환해야 한다"""
    assert parser.can_parse("other@domain.com", "Wiley Alert") is False


def test_wiley_parse_extracts_papers(parser, wiley_html):
    """실제 Wiley fixture HTML에서 1개 이상의 PaperMetadata를 추출해야 한다.
    CrossRef 호출은 mock 처리 (네트워크 불필요).
    """
    with patch("parsers.wiley.crossref_client.lookup_doi", return_value="10.1002/aenm.202500test"):
        papers = parser.parse(wiley_html)
    assert isinstance(papers, list)
    assert len(papers) > 0


def test_wiley_parse_doi_format(parser, wiley_html):
    """추출된 모든 DOI가 '10.'으로 시작해야 한다 (CrossRef mock 사용)"""
    with patch("parsers.wiley.crossref_client.lookup_doi", return_value="10.1002/aenm.202500test"):
        papers = parser.parse(wiley_html)
    assert len(papers) > 0
    for paper in papers:
        if paper.doi:
            assert paper.doi.startswith("10."), f"잘못된 DOI 형식: {paper.doi!r}"


def test_wiley_parse_title_not_empty(parser, wiley_html):
    """추출된 모든 논문 제목이 빈 문자열이 아니어야 한다"""
    with patch("parsers.wiley.crossref_client.lookup_doi", return_value=""):
        papers = parser.parse(wiley_html)
    assert len(papers) > 0
    for paper in papers:
        assert paper.title.strip() != "", "빈 제목이 있음"


def test_wiley_parse_no_duplicate_doi(parser, wiley_html):
    """DOI가 있는 논문들 사이에 DOI 중복이 없어야 한다"""
    fake_dois = [f"10.1002/aenm.{i:08d}" for i in range(100)]
    with patch("parsers.wiley.crossref_client.lookup_doi", side_effect=fake_dois):
        papers = parser.parse(wiley_html)
    dois = [p.doi for p in papers if p.doi]
    assert len(dois) == len(set(dois)), "DOI 중복 발생"


def test_wiley_parse_failure_returns_empty(parser):
    """잘못된 HTML 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("bad html")
    assert result == []


def test_wiley_parse_empty_returns_empty(parser):
    """빈 문자열 입력 시 예외 없이 빈 리스트를 반환해야 한다"""
    result = parser.parse("")
    assert result == []


def test_wiley_parse_crossref_called_for_title(parser, wiley_html):
    """Wiley 파서가 CrossRef API를 호출하여 DOI를 조회해야 한다"""
    with patch("parsers.wiley.crossref_client.lookup_doi", return_value="10.1002/aenm.test") as mock_lookup:
        papers = parser.parse(wiley_html)
    if papers:
        assert mock_lookup.called, "CrossRef lookup_doi가 호출되지 않음"
