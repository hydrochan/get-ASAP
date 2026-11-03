"""parsers/filters.py 라벨 접두 제목 보강 테스트.

운영 DB(라이브 82,356행) 실측: 제목 앞에 비논문 라벨을 단 행 31건이 is_valid_paper_title 을 통과하고 있었다.
(reply to:, response to comment/letter, comments on, editorial note/for/대시, preface:, addendum, correction for,
corrigendum on, news spotlights 등) 거절 사례와, 라벨처럼 보이지만 연구 논문인 사례(회귀)를 함께 고정한다.
"""
import pytest

from parsers.filters import is_valid_paper_title



# 따옴표·대시는 직선/곡선/하이픈 변형이 섞여 들어오므로 그대로 고정한다.
@pytest.mark.parametrize("title", [
    # reply to: (기존 "reply to " 는 공백이 필요해 콜론형을 놓쳤다)
    "Reply to: Evidence of widespread sequencing errors in global avian influenza study",
    # 논평·편집자 서신에 대한 답변 (따옴표·관사 변형)
    "Response to “Comment on ‘Three-Principal-Substrate SERS Profiling of Biofluids’”",
    "Response to Letter to the Editor Regarding “Is Persistence a Hazard?”",
    "Response to comment on the thermal stability of lithium-ion cells",
    "Response to “comment on the thermal stability of lithium-ion cells",
    "Response to \"comment on the thermal stability of lithium-ion cells",
    "Response to ‘comment on the thermal stability of lithium-ion cells",
    "Response to the comment on the thermal stability of lithium-ion cells",
    "Response to letter regarding the thermal stability of lithium-ion cells",
    "Response to the letter regarding the thermal stability of lithium-ion cells",
    "Response to “letter regarding the thermal stability of lithium-ion cells",
    # comments on
    "Comments on authors' response to discussion of the kinetic model",
    "Comments on misuse of iron-57 Mossbauer spectroscopy in catalysis",
    "Comments on “surface-based brain age index for cortical thickness",
    # editorial 변형 (콜론·노트·for·대시 - 공백 유무)
    "Editorial Note: ‘Biology-inspired materials for energy conversion’",
    "Editorial note: ‘Bio-membrane interfaces for ion transport’",
    "Editorial for the Special Issue in Honor of Professor John Smith",
    "Editorial—Special Issue on the NCCR Bio‐Inspired Materials",
    "Editorial — Special Issue on the NCCR Bio-Inspired Materials",
    "Editorial – Special Issue on the NCCR Bio-Inspired Materials",
    "Editorial - Special Issue on the NCCR Bio-Inspired Materials",
    "Editorial: Special Issue on the NCCR Bio-Inspired Materials",
    "Editorial",
    # 나머지 라벨
    "Preface: CIS-10 on Zeolites and Catalysis",
    "Addendum to “Ultrathin metal-organic framework nanosheets for sensing”",
    "Addendum: Ultrathin metal-organic framework nanosheets for sensing",
    "Correction for “Considerations for Gas-phase Hydrogen Production”",
    "Correction for \"Considerations for Gas-phase Hydrogen Production\"",
    "Correction for ‘Considerations for Gas-phase Hydrogen Production’",
    "Correction for'Considerations for Gas-phase Hydrogen Production'",
    "Correction for“Considerations for Gas-phase Hydrogen Production”",
    "Corrigendum on “Inhibiting corrosion of steel in acidic media”",
    "Erratum on “Inhibiting corrosion of steel in acidic media”",
    "News Spotlights, powered by C&EN",
    "Author Correction: Alpha beta gamma delta study",
    "Publisher Correction: Alpha beta gamma delta study",
    "Retraction Note: Alpha beta gamma delta study",
    "In Memoriam: Professor Jane Doe and her contributions",
    "Obituary: Professor Jane Doe and her contributions",
])
def test_label_titles_are_excluded(title):
    assert is_valid_paper_title(title) is False


@pytest.mark.parametrize("title", [
    # 라벨처럼 보이지만 연구 논문 (운영 DB 실측)
    "Editorial and peer review dynamics at elite general science journals",
    "Response to geographic song variation predicts differential gene expression in the blood of a songbird",
    "Introduction to advanced semiconductor nanocrystals",
    # 접두가 단어 경계에 걸려야 한다 - 라벨로 시작하는 단어의 일반 제목
    "Correctional facility air quality and respiratory outcomes in prisoners",
    "Editorials and public opinion on carbon pricing in Europe",
    "Editorially curated datasets improve reproducibility of catalyst benchmarks",
    "Addenda to the standard model of catalyst deactivation kinetics",
    "Corrections for finite-size effects in molecular dynamics simulations",
    "Commentary on responses to environmental stress in rice seedlings",
    "Prefaces and postscripts in historical chemistry textbooks",
    "Responses of soil microbial communities to nitrogen deposition",
    "Reply latency in reaction networks governed by slow modes",
    "Obituaries as a data source for mortality modelling in chemistry",
    # "correction for" 는 뒤에 따옴표로 인용한 제목이 올 때만 정정 공지
    "Correction for multiple testing in genome-wide association studies",
    "Correction of chromatic aberration in electron microscopy of catalysts",
])
def test_label_lookalike_research_titles_stay_valid(title):
    assert is_valid_paper_title(title) is True
