"""parsers.base.title_text 회귀 테스트 - 합성 HTML만 쓴다.

get_text(separator=" ")는 인라인 태그 경계마다 공백을 넣어 CO<sub>2</sub> 를 "CO 2" 로 쪼갰다. title_text 는 블록 태그에만
공백을 넣고, 위·아래첨자·이탤릭 경계의 공백은 규칙(sub 양옆 제거 / sup 앞 보존 / 닫는 부호·하이픈 단어 앞 제거)으로 정리한다.
"""
import pytest
from bs4 import BeautifulSoup

from parsers.base import title_text


def text_of(inner: str, root: str = "a") -> str:
    return title_text(BeautifulSoup(f"<{root}>{inner}</{root}>", "lxml").find(root))


@pytest.mark.parametrize("html, expected", [
    # 기본: 아래·윗첨자는 붙인다
    ("CO<sub>2</sub> reduction", "CO2 reduction"),
    ("Zn<sup>2+</sup>", "Zn2+"),
    ("MoO<sub>4</sub><sup>2–</sup>", "MoO42–"),
    ("Cu<sup>δ+</sup>-Enriched", "Cuδ+-Enriched"),
    ("H<sub>2</sub>O<sub>2</sub> synthesis", "H2O2 synthesis"),
    # 첨자 안팎의 개행·공백
    ("Ge<sub>\n\n1−<em>x</em></sub>Se", "Ge1−xSe"),
    ("CO\n<sub>2</sub> capture", "CO2 capture"),
    ("CO <sub> 2 </sub> capture", "CO2 capture"),
    ("CF<sub>3</sub>\n -substituted", "CF3-substituted"),
    ("SnO<sub>2</sub> /TiO<sub>2</sub> heterojunction", "SnO2/TiO2 heterojunction"),
    ("Ti<sub>3</sub>C<sub>2</sub> , Mo<sub>2</sub> ;", "Ti3C2, Mo2;"),
    ("Mo<sub>2</sub>C ;", "Mo2C ;"),  # 공백 제거는 첨자 바로 뒤에서만
    ("Fe<sub>3</sub>O<sub>4</sub> <sup>2+</sup>", "Fe3O42+"),
    # 이탤릭
    ("1<em>H</em>\n\n-pyrazole", "1H-pyrazole"),
    ("<i>n</i> -Butyl esters", "n-Butyl esters"),
    ("the <i>in situ</i> study", "the in situ study"),
    ("Mn(<i>II</i>) complexes", "Mn(II) complexes"),
    ("Cl<sub>6</sub><em>via</em> route", "Cl6 via route"),
    # 윗첨자 앞 공백은 보존한다
    ("Tracking by <sup>13</sup>C NMR", "Tracking by 13C NMR"),
    ("binding to <sup>*</sup>COH species", "binding to *COH species"),
    # 일반 단어 사이의 공백·문장부호는 그대로
    ("CO<sub>2</sub> to CO<sub>2</sub>-derived products", "CO2 to CO2-derived products"),
    ("Water vs. CO<sub>2</sub>: a study", "Water vs. CO2: a study"),
])
def test_inline_markup_is_joined_by_rules(html, expected):
    assert text_of(html) == expected


@pytest.mark.parametrize("html, expected", [
    ("first<br>second", "first second"),
    ("first<br/><br/>second", "first second"),
    ("<div>first</div><div>second</div>", "first second"),
    ("<p>first</p><p>second</p>", "first second"),
    ("<span>fi</span><span>rst</span>", "first"),  # 일반 인라인 태그는 붙인다
    ("<b>Bold</b> and <span>plain</span>", "Bold and plain"),
])
def test_block_boundaries_get_a_space_and_plain_inline_tags_do_not(html, expected):
    assert text_of(html) == expected


def test_table_cells_are_separated():
    assert text_of("<tr><td>first</td><td>second</td></tr>", root="table") == "first second"


def test_comments_scripts_and_styles_are_ignored():
    assert text_of("CO<!-- hidden -->2<script>var x = 1;</script><style>.a{}</style> reduction") == "CO2 reduction"


def test_nbsp_and_other_unicode_whitespace_collapse_to_one_space():
    assert text_of("CO\xa0<sub>2</sub>\xa0capture  and  more") == "CO2 capture and more"


@pytest.mark.parametrize("prefix", ["jats:", "mml:", ""])
def test_namespaced_sub_and_sup_tags_behave_like_plain_ones(prefix):
    html = f"Fe <{prefix}sub>3</{prefix}sub>O<{prefix}sub>4</{prefix}sub> and Fe<{prefix}sup>3+</{prefix}sup> ions"
    assert text_of(html) == "Fe3O4 and Fe3+ ions"


def test_plain_text_node_is_normalized():
    soup = BeautifulSoup("<a>  two\n words </a>", "lxml")
    assert title_text(soup.find("a").string) == "two words"


def test_empty_tag_gives_empty_string():
    assert text_of("") == ""
