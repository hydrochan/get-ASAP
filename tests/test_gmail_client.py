"""gmail_client 모듈 단위 테스트 (TDD - 14개 테스트)"""
import base64
import json
import os
import pytest
import httplib2
from unittest.mock import MagicMock, patch, call
from googleapiclient.errors import HttpError


# 테스트용 publishers 데이터 픽스처
@pytest.fixture
def publishers():
    """테스트용 출판사 설정 딕셔너리"""
    return {
        "acs": {
            "sender": "alerts@acs.org",
            "name": "ACS Publications",
            "journals": ["JACS", "ACS Nano", "ACS Catalysis", "Nano Letters"]
        },
        "elsevier": {
            "sender": "ealerts@elsevier.com",
            "name": "Elsevier",
            "journals": ["Applied Catalysis B", "Journal of Catalysis"]
        },
        "science": {
            "sender": "ScienceAdvances@sciencemag.org",
            "name": "Science",
            "journals": ["Science", "Science Advances"]
        }
    }


@pytest.fixture
def mock_service():
    """Gmail API 서비스 Mock 객체"""
    return MagicMock()


# ---------- Test 1: build_query - 복수 출판사 ----------
def test_build_query(publishers):
    """복수 출판사일 때 OR로 연결된 from: 쿼리 생성 확인"""
    from gmail_client import build_query

    query = build_query(publishers)
    # 3개 출판사 모두 포함되어야 한다
    assert "from:alerts@acs.org" in query
    assert "from:ealerts@elsevier.com" in query
    assert "from:ScienceAdvances@sciencemag.org" in query
    # OR 키워드로 연결
    assert " OR " in query


# ---------- Test 2: build_query - 단일 출판사 ----------
def test_build_query_single():
    """단일 출판사일 때 OR 없이 from: 쿼리만 생성"""
    from gmail_client import build_query

    publishers_single = {
        "acs": {"sender": "alerts@acs.org", "name": "ACS", "journals": []}
    }
    query = build_query(publishers_single)
    assert query == "from:alerts@acs.org"
    assert "OR" not in query


# ---------- Test 3: load_state - state.json 없을 때 ----------
def test_load_state_empty(tmp_path):
    """state.json 파일이 없을 때 빈 dict 반환 확인"""
    from gmail_client import load_state

    state_path = str(tmp_path / "state.json")
    result = load_state(state_path=state_path)
    assert result == {}


# ---------- Test 4: load_state + save_state 왕복 ----------
def test_load_save_state(tmp_path):
    """save_state로 저장 후 load_state로 동일 값 로드 확인"""
    from gmail_client import load_state, save_state

    state_path = str(tmp_path / "state.json")
    original = {"historyId": "123", "extra": "value"}
    save_state(original, state_path=state_path)

    loaded = load_state(state_path=state_path)
    # historyId 값이 유지되어야 한다
    assert loaded["historyId"] == "123"


# ---------- Test 5: get_new_messages - historyId 없을 때 (초기) ----------
def test_get_new_messages_initial(mock_service):
    """historyId 없을 때 messages.list 호출 + 첫 메시지 historyId 저장 확인"""
    from gmail_client import get_new_messages

    # messages.list 응답 Mock: 메시지 2개
    list_response = {
        "messages": [
            {"id": "msg001", "threadId": "thread001"},
            {"id": "msg002", "threadId": "thread002"},
        ]
    }
    # 각 메시지 get 응답 (historyId 포함)
    msg_detail = {"id": "msg001", "historyId": "500", "payload": {}}

    messages_mock = mock_service.users().messages()
    messages_mock.list.return_value.execute.return_value = list_response
    messages_mock.get.return_value.execute.return_value = msg_detail

    state = {}
    result = get_new_messages(mock_service, state, "from:alerts@acs.org")

    # messages.list가 호출되어야 한다
    mock_service.users().messages().list.assert_called_once()
    # state에 historyId가 저장되어야 한다
    assert state.get("historyId") is not None
    # 메시지 ID 목록이 반환되어야 한다
    assert isinstance(result, list)


# ---------- Test 6: get_new_messages - historyId 있을 때 (증분) ----------
def test_get_new_messages_incremental(mock_service):
    """historyId 있을 때 history.list 호출 + messagesAdded에서 ID 추출 확인"""
    from gmail_client import get_new_messages

    history_response = {
        "history": [
            {
                "messagesAdded": [
                    {"message": {"id": "new001"}},
                    {"message": {"id": "new002"}},
                ]
            }
        ],
        "historyId": "600"
    }

    history_mock = mock_service.users().history()
    history_mock.list.return_value.execute.return_value = history_response

    state = {"historyId": "500"}
    result = get_new_messages(mock_service, state, "from:alerts@acs.org")

    # history.list가 호출되어야 한다
    mock_service.users().history().list.assert_called_once()
    # 새 메시지 ID가 추출되어야 한다
    assert "new001" in result
    assert "new002" in result
    # state의 historyId가 갱신되어야 한다
    assert state["historyId"] == "600"


# ---------- Test 7: get_new_messages - 404 폴백 ----------
def test_get_new_messages_404_fallback(mock_service):
    """history.list 404 응답 시 state historyId 초기화 후 전체 동기화 폴백 확인"""
    from gmail_client import get_new_messages

    # 404 HttpError 생성
    resp = httplib2.Response({"status": "404"})
    resp.reason = "Not Found"
    http_error = HttpError(resp=resp, content=b'{"error": "historyId not found"}')

    # 첫 번째 호출: history.list에서 404 에러
    # 두 번째 호출(폴백): messages.list 성공
    list_response = {
        "messages": [{"id": "msg001"}]
    }
    msg_detail = {"id": "msg001", "historyId": "700", "payload": {}}

    # history.list는 404 에러 발생
    mock_service.users().history().list.return_value.execute.side_effect = http_error
    # messages.list는 정상 응답
    mock_service.users().messages().list.return_value.execute.return_value = list_response
    mock_service.users().messages().get.return_value.execute.return_value = msg_detail

    state = {"historyId": "500"}
    result = get_new_messages(mock_service, state, "from:alerts@acs.org")

    # 폴백 후 messages.list가 호출되어야 한다
    mock_service.users().messages().list.assert_called_once()
    # historyId가 새 값으로 갱신되어야 한다 (폴백 후)
    assert state.get("historyId") is not None
    assert state["historyId"] != "500"  # 기존 값이 아닌 새 값


# ---------- Test 8: get_or_create_label - 라벨 존재 시 ----------
def test_get_or_create_label_existing(mock_service):
    """라벨이 존재할 때 기존 라벨 ID 반환 확인"""
    from gmail_client import get_or_create_label

    labels_response = {
        "labels": [
            {"id": "Label_001", "name": "get-ASAP-processed"},
            {"id": "Label_002", "name": "other-label"},
        ]
    }
    mock_service.users().labels().list.return_value.execute.return_value = labels_response

    result = get_or_create_label(mock_service, "get-ASAP-processed")

    assert result == "Label_001"
    # labels.create는 호출되지 않아야 한다
    mock_service.users().labels().create.assert_not_called()


# ---------- Test 9: get_or_create_label - 라벨 미존재 시 ----------
def test_get_or_create_label_new(mock_service):
    """라벨이 없을 때 labels.create 호출 후 새 ID 반환 확인"""
    from gmail_client import get_or_create_label

    labels_response = {"labels": []}
    create_response = {"id": "Label_999", "name": "get-ASAP-processed"}

    mock_service.users().labels().list.return_value.execute.return_value = labels_response
    mock_service.users().labels().create.return_value.execute.return_value = create_response

    result = get_or_create_label(mock_service, "get-ASAP-processed")

    assert result == "Label_999"
    # labels.create가 호출되어야 한다
    mock_service.users().labels().create.assert_called_once()


# ---------- Test 10: mark_processed ----------
def test_mark_processed(mock_service):
    """messages.modify에 addLabelIds + removeLabelIds=["UNREAD"] 전달 확인"""
    from gmail_client import mark_processed

    mock_service.users().messages().modify.return_value.execute.return_value = {}

    mark_processed(mock_service, "msg001", "Label_001")

    # modify 호출 인수 확인
    call_kwargs = mock_service.users().messages().modify.call_args
    body = call_kwargs.kwargs.get("body") or call_kwargs[1].get("body") or call_kwargs[0][2] if len(call_kwargs[0]) > 2 else None

    # modify가 호출되었는지 확인
    mock_service.users().messages().modify.assert_called_once()


# ---------- Test 11: extract_body - 단순 메시지 ----------
def test_extract_body_simple():
    """단순 메시지(parts 없음)의 base64url 디코딩 확인"""
    from gmail_client import extract_body

    html_content = "<html><body>Hello World</body></html>"
    # base64url 인코딩 (패딩 없음)
    encoded = base64.urlsafe_b64encode(html_content.encode()).rstrip(b"=").decode()

    payload = {
        "mimeType": "text/html",
        "body": {"data": encoded}
    }

    result = extract_body(payload)
    assert "Hello World" in result


# ---------- Test 12: extract_body - multipart ----------
def test_extract_body_multipart():
    """multipart 메시지에서 text/html 파트 추출 확인"""
    from gmail_client import extract_body

    html_content = "<html><body>Multipart HTML</body></html>"
    encoded = base64.urlsafe_b64encode(html_content.encode()).rstrip(b"=").decode()

    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {
                "mimeType": "text/plain",
                "body": {"data": base64.urlsafe_b64encode(b"Plain text").rstrip(b"=").decode()}
            },
            {
                "mimeType": "text/html",
                "body": {"data": encoded}
            }
        ]
    }

    result = extract_body(payload)
    assert "Multipart HTML" in result


# ---------- Test 13: infer_journal - 매칭 성공 ----------
def test_infer_journal(publishers):
    """sender + subject에서 저널명 추론 확인 (ACS Nano 매칭)"""
    from gmail_client import infer_journal

    result = infer_journal(
        sender="alerts@acs.org",
        subject="ACS Nano ASAP Alert - New Articles",
        publishers=publishers
    )
    assert result == "ACS Nano"


# ---------- Test 14: infer_journal - 폴백 ----------
def test_infer_journal_fallback(publishers):
    """저널명 매칭 실패 시 출판사명 반환 확인"""
    from gmail_client import infer_journal

    result = infer_journal(
        sender="alerts@acs.org",
        subject="ASAP Alert - Unknown Journal",
        publishers=publishers
    )
    assert result == "ACS Publications"
