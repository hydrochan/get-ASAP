"""PaperMetadata 데이터 모델 + publishers.json 스키마 테스트 (TDD RED)"""
import json
import os
import pytest


# ─── Test 1: 기본 필드 생성 ───────────────────────────────────────────────────

def test_paper_metadata_creation():
    """PaperMetadata 생성 시 title, journal, date 필드에 접근 가능해야 한다"""
    from models import PaperMetadata
    paper = PaperMetadata(
        title="Test Paper",
        journal="JACS",
        date="2026-04-03",
    )
    assert paper.title == "Test Paper"
    assert paper.journal == "JACS"
    assert paper.date == "2026-04-03"


# ─── Test 2: 필드 타입 검증 ─────────────────────────────────────────────────

def test_paper_metadata_field_types():
    """PaperMetadata 필드가 문자열 타입이어야 한다"""
    from models import PaperMetadata
    paper = PaperMetadata(
        title="Type Test",
        journal="ACS Nano",
        date="2026-04-03",
    )
    assert isinstance(paper.title, str)
    assert isinstance(paper.journal, str)
    assert isinstance(paper.date, str)


# ─── Test 3: 빈 문자열 허용 ─────────────────────────────────────────────────

def test_paper_metadata_empty_strings():
    """journal, date는 빈 문자열로 초기화 가능해야 한다 (파서 중간 상태)"""
    from models import PaperMetadata
    paper = PaperMetadata(
        title="Partial Paper",
        journal="",
        date="",
    )
    assert paper.title == "Partial Paper"
    assert paper.journal == ""
    assert paper.date == ""


# ─── Test 4: publishers.json 스키마 검증 ────────────────────────────────────

def test_publishers_json_schema():
    """publishers.json 로드 시 각 출판사에 sender, name, journals 키가 있어야 한다"""
    publishers_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "publishers.json"
    )
    with open(publishers_path, "r", encoding="utf-8") as f:
        publishers = json.load(f)

    assert len(publishers) > 0, "publishers.json에 출판사가 하나 이상 있어야 한다"
    for key, pub in publishers.items():
        assert "sender" in pub, f"{key}: sender 키 없음"
        assert "name" in pub, f"{key}: name 키 없음"
        assert "journals" in pub, f"{key}: journals 키 없음"
        assert isinstance(pub["journals"], list), f"{key}: journals는 list여야 한다"


# ─── Test 5: 발신자로 출판사 조회 ───────────────────────────────────────────

def test_publishers_journal_lookup():
    """publishers.json에서 특정 발신자 이메일로 출판사명과 저널 목록을 조회할 수 있어야 한다"""
    publishers_path = os.path.join(
        os.path.dirname(os.path.dirname(__file__)), "publishers.json"
    )
    with open(publishers_path, "r", encoding="utf-8") as f:
        publishers = json.load(f)

    # 발신자로 출판사 찾기
    sender_map = {pub["sender"]: pub for pub in publishers.values()}

    # ACS 발신자로 조회 (실제 확인된 sender: updates@acspubs.org, Plan 01에서 검증)
    acs_pub = sender_map.get("updates@acspubs.org")
    assert acs_pub is not None, "updates@acspubs.org 발신자로 ACS 출판사를 조회할 수 있어야 한다"
    assert acs_pub["name"] == "ACS Publications"
    assert len(acs_pub["journals"]) > 0
