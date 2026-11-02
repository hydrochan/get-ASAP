"""parsers/nature.py 회귀 테스트 - 실제 메일 구조를 본뜬 최소 합성 HTML만 쓴다(실제 메일은 수신자별 추적 링크·주소를 담으므로 커밋 금지).

실제 Nature 메일의 섹션 구조(2026-11 VM 실측):
- 평면 구조 저널(Nature Catalysis / Nature Energy / Nature Chemical Engineering): <h2> Research 아래에 논문이 바로 나열된다.
  Research Highlights / Research Briefings / News & Views / Editorial / Comment & Opinion / Policy Brief /
  Correspondence / Amendments & Corrections 같은 다른 <h2> 섹션은 논문이 아니다.
- 일반 저널(Nature Chemistry / Materials / Nanotechnology / Photonics / Physics): <h2> Articles, Review Articles, Perspectives.
- Nature 본지: <h2> Research는 <h3> News & Views / Reviews / Articles를 품은 컨테이너다. 직속 항목은 Articles와
  겹치지 않는 Research Highlights 성격이라 <h3> Reviews / Articles만 논문으로 센다(News & Views는 제외).
- Nature Communications: 섹션 헤더 없이 <h3>가 논문 제목.
"""
from parsers.nature import NatureParser


def item(title, tag="div"):
    """실제 메일의 논문 항목 구조: td > (div|span) > a."""
    return f'<table><tr><td><{tag}><a href="https://example.invalid/{abs(hash(title))}">{title}</a></{tag}></td></tr></table>'


def section(level, name, titles):
    return f"<{level}>{name}</{level}>" + "".join(item(t) for t in titles)


def mail(*parts):
    return "<html><body><h1>Synthetic Journal Contents: Volume 1 Issue 1</h1>" + "".join(parts) + "</body></html>"


def titles(html):
    return [p.title for p in NatureParser().parse(html)]


RESEARCH = [
    "Synthetic catalyst design enables selective ammonia cracking",
    "Atomic scale imaging of a model electrolyte interface",
    "Reactor level control of heat flow in flat panel devices",
]
NOT_RESEARCH = {
    "Editorial": "Synthetic editorial about community priorities today",
    "News & Views": "Synthetic news and views about a recent result",
    "Research Highlights": "Synthetic highlight about someone else's paper",
    "Research Briefings": "Synthetic briefing summarizing a paper in this issue",
    "Comment & Opinion": "Synthetic comment about funding for the field",
    "Correspondence": "Synthetic correspondence regarding a past article",
    "Matters Arising": "Synthetic matters arising concerning earlier work",
    "Policy Brief": "Synthetic policy brief on residential adoption",
    "Amendments & Corrections": "Author Correction: Synthetic correction to an earlier paper",
}


def test_flat_research_section_is_parsed():
    """Nature Catalysis / Energy / Chemical Engineering: Research 직속 논문이 추출된다(이전엔 0건)."""
    html = mail(
        section("h2", "News & Views", [NOT_RESEARCH["News & Views"]]),
        section("h2", "Research", RESEARCH),
        section("h2", "Amendments & Corrections", [NOT_RESEARCH["Amendments & Corrections"]]),
    )
    papers = NatureParser().parse(html)
    assert [p.title for p in papers] == RESEARCH
    assert all(p.url.startswith("https://example.invalid/") and p.journal == "" for p in papers)


def test_non_research_sections_are_never_parsed_around_research():
    html = mail(*(section("h2", name, [title]) for name, title in NOT_RESEARCH.items()), section("h2", "Research", RESEARCH))
    assert titles(html) == RESEARCH


def test_research_at_the_end_of_the_mail_and_span_wrapped_titles():
    html = mail("<h2>Editorial</h2>" + item(NOT_RESEARCH["Editorial"]), "<h2>Research</h2>" + item(RESEARCH[0], "span") + item(RESEARCH[1]))
    assert titles(html) == RESEARCH[:2]


def test_reviews_and_review_articles_sit_next_to_research():
    review = "Synthetic review of ammonia based energy carriers"
    html = mail(section("h2", "Reviews", [review]), section("h2", "Research", RESEARCH[:1]))
    assert titles(html) == [review, RESEARCH[0]]
    assert titles(mail(section("h2", "Review Articles", [review]), section("h2", "Articles", RESEARCH[:1]))) == [review, RESEARCH[0]]


def test_existing_sections_still_parse():
    html = mail(
        section("h2", "Articles", RESEARCH[:1]),
        section("h2", "Letters", RESEARCH[1:2]),
        section("h2", "Perspectives", RESEARCH[2:]),
    )
    assert titles(html) == RESEARCH


def test_nature_main_research_container_yields_h3_reviews_and_articles():
    """Nature 본지: h2 Research는 h3 하위 섹션을 품은 컨테이너. 직속 항목·News & Views는 논문으로 세지 않고
    h3 Reviews와 Articles는 센다(중복 없이, 메일 순서대로)."""
    teaser = "Synthetic teaser listed directly under the research heading"
    review = "Synthetic review inside the research container"
    html = mail(
        section("h2", "This week", []),
        section("h3", "Research Highlights", ["Synthetic highlight in the weekly section"]),
        "<h2>Research</h2>" + item(teaser),
        section("h3", "News & Views", ["Synthetic news and views piece in a container"]),
        section("h3", "Reviews", [review]),
        section("h3", "Articles", RESEARCH),
        section("h2", "Amendments & Corrections", [NOT_RESEARCH["Amendments & Corrections"]]),
    )
    assert titles(html) == [review, *RESEARCH]


def test_nature_main_h3_review_articles_variant_and_no_duplicates():
    """h3 'Review Articles' 변형도 센다. 같은 제목이 h3 두 곳에 나와도 한 번만 나온다."""
    review = "Synthetic review article inside the research container"
    html = mail(
        "<h2>Research</h2>",
        section("h3", "News & Views", ["Synthetic news and views piece in a container"]),
        section("h3", "Review Articles", [review]),
        section("h3", "Articles", [review, RESEARCH[0]]),
    )
    assert titles(html) == [review, RESEARCH[0]]


def test_nature_communications_h3_titles_are_unchanged():
    """섹션 헤더 없이 h3가 논문 제목인 Nature Communications 형식."""
    html = mail(
        "<h2>Physical sciences ›</h2>",
        *(f"<h3>{t}</h3>" for t in RESEARCH),
        "<h3>Related Subjects</h3>" + item("Chemistry"),
        "<h2>Biological sciences ›</h2>",
        "<h3>Synthetic cell biology result with a long descriptive title</h3>",
    )
    assert titles(html) == [*RESEARCH, "Synthetic cell biology result with a long descriptive title"]


def test_mail_without_paper_sections_returns_nothing():
    assert titles(mail(section("h2", "Editorial", [NOT_RESEARCH["Editorial"]]))) == []
    assert titles("") == []
