"""출판사 파서의 제목 추출 회귀 테스트 - 최소 합성 HTML만 쓴다(실제 메일은 수신자별 추적 링크를 담으므로 커밋 금지).

예전에는 get_text(separator=" ")라 CO<sub>2</sub> 가 "CO 2" 로 저장됐다. 각 파서가 title_text 를 써서 "CO2" 로 뽑는지 본다.
Nature 는 test_parser_nature.py 에 있다.
"""
import pytest

from parsers.acs import ACSParser
from parsers.cellpress import CellPressParser
from parsers.elsevier import ElsevierParser
from parsers.rsc import RSCParser
from parsers.science import ScienceParser
from parsers.wiley import WileyParser

SPLIT = "Electroreduction of CO<sub>2</sub> to ethylene on <i>n</i>-type Cu<sup>δ+</sup> catalysts"
JOINED = "Electroreduction of CO2 to ethylene on n-type Cuδ+ catalysts"
URL = "https://example.invalid/paper"


def html_page(body: str) -> str:
    return f"<html><body>{body}</body></html>"


def titles(parser, body: str) -> list[str]:
    return [p.title for p in parser.parse(html_page(body))]


def test_acs_tolkien_title():
    body = f'<table class="tolkien-column-9"><tr><td><h5><a href="{URL}">{SPLIT}</a></h5></td></tr></table>'
    assert titles(ACSParser(), body) == [JOINED]


def test_acs_old_ealerts_title():
    assert titles(ACSParser(), f'<p><strong><a href="{URL}">{SPLIT}</a></strong></p>') == [JOINED]


def test_acs_silverchair_title_is_unchanged():
    body = (
        "<table><tbody>"
        f"<tr><td>{SPLIT}</td></tr>"
        '<tr><td><strong>DOI:</strong> <a href="https://doi.org/10.1021/x.1">10.1021/x.1</a></td></tr>'
        "</tbody></table>"
    )
    assert titles(ACSParser(), body) == [JOINED]


def test_cellpress_title():
    assert titles(CellPressParser(), f'<table><tr><td><a href="https://www.cell.com/x">{SPLIT}</a></td></tr></table>') == [JOINED]


def test_elsevier_title():
    assert titles(ElsevierParser(), f'<h2><a href="{URL}">{SPLIT}</a></h2>') == [JOINED]


def test_science_title():
    body = (
        '<table><tr><td class="em_txt_grey">Research Article|Chemistry</td></tr>'
        f'<tr><td class="em_f24"><a href="{URL}">{SPLIT}</a></td></tr></table>'
    )
    assert titles(ScienceParser(), body) == [JOINED]


def test_wiley_title_with_and_without_h5():
    body = (
        f'<a class="issue-item__title" href="{URL}/1"><h5>{SPLIT}</h5></a>'
        f'<a class="issue-item__title" href="{URL}/2">Another {SPLIT} (Adv. Mater. 2026)</a>'
    )
    assert titles(WileyParser(), body) == [JOINED, "Another " + JOINED]


def test_rsc_old_template_title_and_section_heading_filter():
    body = (
        '<table><tr><td class="GroupHeading">Papers</td></tr>'
        f'<tr><td><a class="ItemTitleLink" href="{URL}/1">{SPLIT}</a></td></tr>'
        '<tr><td class="GroupHeading">Comments</td></tr>'
        f'<tr><td><a class="ItemTitleLink" href="{URL}/2">{SPLIT} (comment)</a></td></tr></table>'
    )
    assert titles(RSCParser(), body) == [JOINED]


def test_rsc_new_template_title_and_citation_line():
    section = 'style="font-size:18px;line-height:32px"'
    title = 'style="font-size:22px;line-height:1.4em"'
    cite = 'style="font-size:17px;line-height:20px;color:#00436d"'
    body = (
        f"<table><tr><td {section}>Papers</td></tr>"
        f'<tr><td {title}><a href="{URL}">{SPLIT}</a></td></tr>'
        f"<tr><td {cite}>J. Synth. Chem., 2026, 1, 1-9, DOI: 10.1039/X</td></tr></table>"
    )
    assert titles(RSCParser(), body) == [JOINED]


@pytest.mark.parametrize("parser", [ACSParser(), CellPressParser(), ElsevierParser(), ScienceParser(), WileyParser(), RSCParser()])
def test_empty_mail_returns_nothing(parser):
    assert parser.parse("") == []
