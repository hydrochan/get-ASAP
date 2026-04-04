"""main.py 파이프라인 오케스트레이터 단위 테스트 (TDD - 6개 이상 테스트)

모든 외부 모듈은 mock 처리하여 순수 로직만 검증.
"""
import sys
import pytest
from unittest.mock import MagicMock, patch, call


# ---------- 공통 픽스처 ----------

@pytest.fixture
def mock_publishers():
    """테스트용 publishers 딕셔너리"""
    return {
        "acs": {
            "sender": "updates@acspubs.org",
            "name": "ACS Publications",
            "journals": ["JACS", "ACS Catalysis"],
        }
    }


@pytest.fixture
def mock_paper():
    """테스트용 PaperMetadata 객체 (journal 비어있어 infer_journal 호출 유도)"""
    from models import PaperMetadata
    return PaperMetadata(
        title="Test Paper Title",
        doi="",
        journal="",    # 비어있어야 infer_journal이 호출됨
        date="2026-04-04",
    )


@pytest.fixture
def mock_paper_no_doi():
    """DOI가 없는 테스트용 PaperMetadata 객체"""
    from models import PaperMetadata
    return PaperMetadata(
        title="Test Paper No DOI",
        doi="",
        journal="",
        date="2026-04-04",
    )


# ---------- Test 1: dry_run=True 시 save_papers 미호출 ----------

def test_dry_run_does_not_call_save_papers(mock_publishers, mock_paper):
    """dry_run=True 시 save_papers가 호출되지 않아야 한다"""
    mock_parser = MagicMock()
    mock_parser.publisher_name = "ACS Publications"
    mock_parser.parse.return_value = [mock_paper]

    mock_msg = {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "updates@acspubs.org"},
                {"name": "Subject", "value": "JACS ASAP"},
            ],
            "body": {"data": ""},
        }
    }
    mock_service = MagicMock()
    mock_service.users().messages().get().execute.return_value = mock_msg

    with patch("main.get_gmail_service", return_value=mock_service), \
         patch("main.build_query", return_value="from:updates@acspubs.org"), \
         patch("main.load_state", return_value={}), \
         patch("main.save_state"), \
         patch("main.get_new_messages", return_value=["msg_001"]), \
         patch("main.load_parsers", return_value=[mock_parser]), \
         patch("main.extract_body", return_value="<html>test</html>"), \
         patch("main.infer_journal", return_value="ACS Catalysis"), \
         patch("main.save_papers") as mock_save, \
         patch("main.get_or_create_db", return_value="db_123"), \
         patch("main.get_or_create_label", return_value="label_123"), \
         patch("main.mark_processed"), \
         patch("main._load_publishers", return_value=mock_publishers):

        from main import run_pipeline
        run_pipeline(dry_run=True)

        mock_save.assert_not_called()


# ---------- Test 2: dry_run=True 시 mark_processed 미호출 ----------

def test_dry_run_does_not_call_mark_processed(mock_publishers, mock_paper):
    """dry_run=True 시 mark_processed가 호출되지 않아야 한다"""
    mock_parser = MagicMock()
    mock_parser.publisher_name = "ACS Publications"
    mock_parser.parse.return_value = [mock_paper]

    mock_msg = {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "updates@acspubs.org"},
                {"name": "Subject", "value": "JACS ASAP"},
            ],
            "body": {"data": ""},
        }
    }
    mock_service = MagicMock()
    mock_service.users().messages().get().execute.return_value = mock_msg

    with patch("main.get_gmail_service", return_value=mock_service), \
         patch("main.build_query", return_value="from:updates@acspubs.org"), \
         patch("main.load_state", return_value={}), \
         patch("main.save_state"), \
         patch("main.get_new_messages", return_value=["msg_001"]), \
         patch("main.load_parsers", return_value=[mock_parser]), \
         patch("main.extract_body", return_value="<html>test</html>"), \
         patch("main.infer_journal", return_value="ACS Catalysis"), \
         patch("main.save_papers"), \
         patch("main.get_or_create_db", return_value="db_123"), \
         patch("main.get_or_create_label", return_value="label_123"), \
         patch("main.mark_processed") as mock_mark, \
         patch("main._load_publishers", return_value=mock_publishers):

        from main import run_pipeline
        run_pipeline(dry_run=True)

        mock_mark.assert_not_called()


# ---------- Test 3: dry_run=False 시 전체 파이프라인 순서 검증 ----------

def test_full_pipeline_order(mock_publishers, mock_paper):
    """dry_run=False 시 전체 파이프라인 순서 검증:
    get_gmail_service -> build_query -> get_new_messages ->
    extract_body -> parser.parse -> save_papers ->
    mark_processed -> save_state
    """
    call_order = []

    mock_parser = MagicMock()
    mock_parser.publisher_name = "ACS Publications"
    mock_parser.parse.side_effect = lambda x: (call_order.append("parse"), [mock_paper])[1]

    mock_msg = {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "updates@acspubs.org"},
                {"name": "Subject", "value": "JACS ASAP"},
            ],
            "body": {"data": ""},
        }
    }
    mock_service = MagicMock()
    mock_service.users().messages().get().execute.return_value = mock_msg

    def track(name, retval):
        def fn(*args, **kwargs):
            call_order.append(name)
            return retval
        return fn

    with patch("main.get_gmail_service", side_effect=track("get_gmail_service", mock_service)), \
         patch("main.build_query", side_effect=track("build_query", "from:updates@acspubs.org")), \
         patch("main.load_state", side_effect=track("load_state", {})), \
         patch("main.save_state", side_effect=track("save_state", None)), \
         patch("main.get_new_messages", side_effect=track("get_new_messages", ["msg_001"])), \
         patch("main.load_parsers", side_effect=track("load_parsers", [mock_parser])), \
         patch("main.extract_body", side_effect=track("extract_body", "<html>test</html>")), \
         patch("main.infer_journal", return_value="ACS Catalysis"), \
         patch("main.save_papers", side_effect=track("save_papers", {"saved": 1, "skipped": 0, "failed": 0})), \
         patch("main.get_or_create_db", side_effect=track("get_or_create_db", "db_123")), \
         patch("main.get_or_create_label", side_effect=track("get_or_create_label", "label_123")), \
         patch("main.mark_processed", side_effect=track("mark_processed", None)), \
         patch("main._load_publishers", return_value=mock_publishers):

        from main import run_pipeline
        result = run_pipeline(dry_run=False)

    # 핵심 순서 검증
    assert "get_gmail_service" in call_order
    assert "build_query" in call_order
    assert "get_new_messages" in call_order
    assert "extract_body" in call_order
    assert "parse" in call_order
    assert "save_papers" in call_order
    assert "mark_processed" in call_order
    assert "save_state" in call_order

    # 순서 보장: save_papers는 parse 이후
    assert call_order.index("save_papers") > call_order.index("parse")
    # mark_processed는 save_papers 이후
    assert call_order.index("mark_processed") > call_order.index("save_papers")
    # save_state는 mark_processed 이후
    assert call_order.index("save_state") > call_order.index("mark_processed")

    # 반환값 검증
    assert result["extracted"] == 1
    assert result["saved"] == 1


# ---------- Test 4: 개별 메일 파싱 에러 시 해당 메일만 스킵 ----------

def test_single_mail_error_is_skipped(mock_publishers, mock_paper):
    """개별 메일 파싱 에러 시 해당 메일만 스킵하고 다음 메일은 정상 처리"""
    mock_parser = MagicMock()
    mock_parser.publisher_name = "ACS Publications"

    # 첫 번째 메일: 파싱 에러 발생
    # 두 번째 메일: 정상 처리
    parse_call_count = [0]

    def parse_side_effect(body):
        parse_call_count[0] += 1
        if parse_call_count[0] == 1:
            raise ValueError("파싱 실패 시뮬레이션")
        return [mock_paper]

    mock_parser.parse.side_effect = parse_side_effect

    mock_msg = {
        "payload": {
            "mimeType": "text/html",
            "headers": [
                {"name": "From", "value": "updates@acspubs.org"},
                {"name": "Subject", "value": "JACS ASAP"},
            ],
            "body": {"data": ""},
        }
    }
    mock_service = MagicMock()
    mock_service.users().messages().get().execute.return_value = mock_msg

    with patch("main.get_gmail_service", return_value=mock_service), \
         patch("main.build_query", return_value="from:updates@acspubs.org"), \
         patch("main.load_state", return_value={}), \
         patch("main.save_state"), \
         patch("main.get_new_messages", return_value=["msg_001", "msg_002"]), \
         patch("main.load_parsers", return_value=[mock_parser]), \
         patch("main.extract_body", return_value="<html>test</html>"), \
         patch("main.infer_journal", return_value="ACS Catalysis"), \
         patch("main.save_papers", return_value={"saved": 1, "skipped": 0, "failed": 0}), \
         patch("main.get_or_create_db", return_value="db_123"), \
         patch("main.get_or_create_label", return_value="label_123"), \
         patch("main.mark_processed"), \
         patch("main._load_publishers", return_value=mock_publishers):

        from main import run_pipeline
        # 에러가 있어도 전체 파이프라인이 계속 진행되어야 한다
        result = run_pipeline(dry_run=False)

    # 두 번째 메일은 정상 처리됨 -> 1건 추출
    assert result["extracted"] == 1


# ---------- Test 5: parse_args --dry-run, --verbose 옵션 파싱 ----------

def test_parse_args_dry_run_verbose():
    """parse_args()가 --dry-run, --verbose 옵션을 올바르게 파싱한다"""
    from main import parse_args

    # --dry-run 옵션
    args = parse_args(["--dry-run"])
    assert args.dry_run is True
    assert args.verbose is False

    # --verbose 옵션
    args = parse_args(["--verbose"])
    assert args.verbose is True
    assert args.dry_run is False

    # 두 옵션 동시
    args = parse_args(["--dry-run", "--verbose"])
    assert args.dry_run is True
    assert args.verbose is True

    # 옵션 없음 (기본값)
    args = parse_args([])
    assert args.dry_run is False
    assert args.verbose is False


# ---------- Test 6: 메일 0건일 때 조기 종료 ----------

def test_no_messages_early_return(mock_publishers):
    """새 메일이 0건일 때 Notion/라벨 코드가 실행되지 않아야 한다"""
    mock_service = MagicMock()

    with patch("main.get_gmail_service", return_value=mock_service), \
         patch("main.build_query", return_value="from:updates@acspubs.org"), \
         patch("main.load_state", return_value={}), \
         patch("main.save_state") as mock_save_state, \
         patch("main.get_new_messages", return_value=[]), \
         patch("main.load_parsers") as mock_load_parsers, \
         patch("main.save_papers") as mock_save_papers, \
         patch("main.get_or_create_db") as mock_get_db, \
         patch("main.get_or_create_label") as mock_get_label, \
         patch("main.mark_processed") as mock_mark, \
         patch("main._load_publishers", return_value=mock_publishers):

        from main import run_pipeline
        result = run_pipeline(dry_run=False)

    # 메일이 없으면 파서 로드, Notion 저장, 라벨 마킹 모두 미실행
    mock_load_parsers.assert_not_called()
    mock_save_papers.assert_not_called()
    mock_get_db.assert_not_called()
    mock_get_label.assert_not_called()
    mock_mark.assert_not_called()

    # save_state는 조기 종료 시에도 호출되어야 한다
    mock_save_state.assert_called_once()

    # 결과 검증
    assert result["extracted"] == 0


# ---------- Test 7 (보너스): _find_publisher_key 헬퍼 함수 ----------

def test_find_publisher_key(mock_publishers):
    """_find_publisher_key가 sender로 publisher key를 올바르게 반환한다"""
    from main import _find_publisher_key

    # ACS 발신자 매칭
    key = _find_publisher_key("updates@acspubs.org", mock_publishers)
    assert key == "acs"

    # 없는 발신자
    key = _find_publisher_key("unknown@example.com", mock_publishers)
    assert key is None
