"""Gmail API 클라이언트 모듈 (per D-01, D-03, D-04, D-06)

필터링, 증분 동기화(historyId), 라벨 부여, 본문 디코딩, 저널명 추론을 제공한다.
"""
import base64
import json
import os
from datetime import datetime, timezone
from googleapiclient.errors import HttpError


def build_query(publishers: dict) -> str:
    """publishers.json 데이터에서 Gmail 검색 쿼리 생성 (per D-01)

    Args:
        publishers: 출판사 설정 딕셔너리 (sender 키 필수)

    Returns:
        Gmail 검색 쿼리 문자열 예: "from:alerts@acs.org OR from:ealerts@elsevier.com"
    """
    # 각 출판사의 sender 이메일을 "from:XXX" 형식으로 변환 (배열 지원)
    from_filters = []
    for pub in publishers.values():
        senders = pub["sender"]
        if isinstance(senders, str):
            senders = [senders]
        for s in senders:
            from_filters.append(f"from:{s}")

    return " OR ".join(from_filters)


def load_state(state_path: str = "state.json") -> dict:
    """state.json 파일에서 실행 상태 로드 (per D-03)

    historyId 등 증분 동기화에 필요한 상태를 영속화 파일에서 읽어온다.

    Args:
        state_path: state.json 파일 경로 (기본값: "state.json")

    Returns:
        state 딕셔너리. 파일이 없으면 빈 dict 반환.
    """
    if not os.path.exists(state_path):
        # 첫 실행 시 state.json이 없는 경우 → 초기 상태
        return {}

    with open(state_path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_state(state: dict, state_path: str = "state.json") -> None:
    """state dict를 state.json에 저장 (per D-03)

    lastRunAt 필드를 현재 UTC 시각으로 자동 갱신한다.

    Args:
        state: 저장할 상태 딕셔너리 (historyId 포함)
        state_path: state.json 파일 경로 (기본값: "state.json")
    """
    # lastRunAt을 현재 UTC ISO 형식으로 자동 갱신
    state["lastRunAt"] = datetime.now(timezone.utc).isoformat()

    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def get_new_messages(service, state: dict, query: str) -> list[str]:
    """Gmail API를 통해 새 메일 ID 목록 반환 (per D-03)

    증분 동기화 흐름:
    1. state에 historyId 없음 → 초기 동기화:
       messages.list(q=query)로 최신 메일의 historyId를 state에 저장
    2. state에 historyId 있음 → 증분 동기화:
       history.list(startHistoryId=N)으로 신규 메시지만 추출
    3. HttpError 404 → historyId 무효화 후 전체 동기화로 폴백

    Args:
        service: Gmail API 서비스 객체 (auth.py의 get_gmail_service() 반환값)
        state: 현재 실행 상태 (historyId 포함 가능)
        query: Gmail 검색 쿼리 (build_query() 반환값)

    Returns:
        새 메일의 Gmail message ID 목록
    """
    history_id = state.get("historyId")

    if not history_id:
        # ── 초기 동기화: messages.list로 전체 검색 ──
        # historyId 기준점을 확보하기 위해 첫 번째 메시지의 historyId를 저장한다
        return _initial_sync(service, state, query)
    else:
        # ── 증분 동기화: history.list로 변경분만 가져오기 ──
        try:
            return _incremental_sync(service, state, history_id)
        except HttpError as e:
            if e.resp.status == 404 or str(e.resp["status"]) == "404":
                # historyId가 만료되었거나 서버에서 찾을 수 없는 경우
                # → historyId 초기화 후 전체 동기화로 폴백 (per D-03)
                state["historyId"] = None
                return get_new_messages(service, state, query)
            raise


def _initial_sync(service, state: dict, query: str) -> list[str]:
    """초기 동기화: messages.list로 전체 검색 후 historyId 기준점 확보

    첫 번째 메시지의 historyId를 state에 저장하여 다음 실행부터 증분 동기화 가능하게 한다.
    """
    message_ids = []
    page_token = None

    # 모든 페이지 순회
    while True:
        kwargs = {"userId": "me", "q": query}
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.users().messages().list(**kwargs).execute()
        messages = response.get("messages", [])
        message_ids.extend([m["id"] for m in messages])

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    # 첫 번째 메시지의 historyId를 기준점으로 저장
    # (다음 실행부터 이 historyId 이후 변경분만 가져옴)
    if message_ids:
        first_msg = service.users().messages().get(
            userId="me", id=message_ids[0], format="minimal"
        ).execute()
        state["historyId"] = first_msg.get("historyId")

    return message_ids


def _incremental_sync(service, state: dict, start_history_id: str) -> list[str]:
    """증분 동기화: history.list로 lastHistoryId 이후 추가된 메일만 가져오기

    Args:
        service: Gmail API 서비스 객체
        state: 실행 상태 (historyId 갱신됨)
        start_history_id: 마지막 실행의 historyId (이후 변경분만 반환)

    Returns:
        새로 추가된 메시지 ID 목록
    """
    message_ids = []
    page_token = None

    while True:
        kwargs = {
            "userId": "me",
            "startHistoryId": start_history_id,
            "historyTypes": ["messageAdded"],
        }
        if page_token:
            kwargs["pageToken"] = page_token

        response = service.users().history().list(**kwargs).execute()

        # messagesAdded 이벤트에서 새 메시지 ID 추출
        for history_item in response.get("history", []):
            for added in history_item.get("messagesAdded", []):
                msg_id = added["message"]["id"]
                message_ids.append(msg_id)

        # 응답의 historyId로 state 갱신 (다음 실행 기준점)
        if "historyId" in response:
            state["historyId"] = response["historyId"]

        page_token = response.get("nextPageToken")
        if not page_token:
            break

    return message_ids


def get_or_create_label(service, label_name: str = "get-ASAP-processed") -> str:
    """Gmail 라벨 조회 또는 신규 생성 (per D-04)

    "get-ASAP-processed" 라벨이 존재하면 ID를 반환하고,
    없으면 labels.create로 생성 후 새 ID를 반환한다.

    Args:
        service: Gmail API 서비스 객체
        label_name: 조회/생성할 라벨 이름 (기본값: "get-ASAP-processed")

    Returns:
        라벨 ID 문자열
    """
    # 기존 라벨 목록 조회
    response = service.users().labels().list(userId="me").execute()
    labels = response.get("labels", [])

    # 이름으로 기존 라벨 검색
    for label in labels:
        if label["name"] == label_name:
            return label["id"]

    # 라벨이 없으면 새로 생성
    new_label = service.users().labels().create(
        userId="me",
        body={
            "name": label_name,
            "messageListVisibility": "show",
            "labelListVisibility": "labelShow",
        }
    ).execute()

    return new_label["id"]


def mark_processed(service, message_id: str, label_id: str) -> None:
    """메일에 처리 완료 라벨을 부여하고 읽음 처리 (per D-04)

    addLabelIds: 처리 완료 라벨 추가
    removeLabelIds: UNREAD 라벨 제거 (읽음 처리)

    Args:
        service: Gmail API 서비스 객체
        message_id: 처리할 Gmail 메시지 ID
        label_id: 부여할 라벨 ID (get_or_create_label() 반환값)
    """
    service.users().messages().modify(
        userId="me",
        id=message_id,
        body={
            "addLabelIds": [label_id],
            "removeLabelIds": ["UNREAD"],  # 읽음 처리
        }
    ).execute()


def extract_body(payload: dict) -> str:
    """Gmail 메시지 payload에서 본문 텍스트 추출 및 base64url 디코딩

    처리 순서:
    1. multipart 메시지: parts 재귀 탐색 → text/html 우선, 없으면 text/plain
    2. 단순 메시지: payload.body.data를 직접 디코딩

    Args:
        payload: Gmail API 메시지의 payload 필드

    Returns:
        디코딩된 본문 문자열. 본문이 없으면 빈 문자열 반환.
    """
    mime_type = payload.get("mimeType", "")

    if mime_type.startswith("multipart/"):
        # multipart 메시지: parts를 재귀적으로 탐색
        parts = payload.get("parts", [])

        # text/html 파트 우선 탐색
        for part in parts:
            if part.get("mimeType") == "text/html":
                return _decode_body_data(part.get("body", {}).get("data", ""))

        # text/html 없으면 text/plain 탐색
        for part in parts:
            if part.get("mimeType") == "text/plain":
                return _decode_body_data(part.get("body", {}).get("data", ""))

        # 중첩 multipart 재귀 탐색
        for part in parts:
            if part.get("mimeType", "").startswith("multipart/"):
                result = extract_body(part)
                if result:
                    return result

        return ""

    else:
        # 단순 메시지: payload.body.data 직접 디코딩
        data = payload.get("body", {}).get("data", "")
        return _decode_body_data(data)


def _decode_body_data(data: str) -> str:
    """base64url 인코딩된 데이터 디코딩

    Gmail API는 메일 본문을 base64url 인코딩(패딩 없음)으로 반환한다.
    Python의 urlsafe_b64decode는 패딩이 필요하므로 "=="를 추가한다.

    Args:
        data: base64url 인코딩된 문자열 (패딩 없음)

    Returns:
        디코딩된 UTF-8 문자열
    """
    if not data:
        return ""

    # base64url 디코딩: 패딩 "==" 추가 후 디코딩 (per RESEARCH.md)
    decoded_bytes = base64.urlsafe_b64decode(data + "==")
    return decoded_bytes.decode("utf-8", errors="replace")


def infer_journal(sender: str, subject: str, publishers: dict) -> str:
    """발신자와 제목에서 저널명 추론 (per D-06)

    추론 순서:
    1. publishers dict에서 sender 이메일로 출판사 찾기
    2. 해당 출판사의 journals 리스트에서 subject에 포함된 저널명 탐색
    3. 매칭되면 저널명 반환, 아니면 출판사 name 반환 (폴백)

    Args:
        sender: 발신자 이메일 주소
        subject: 메일 제목
        publishers: 출판사 설정 딕셔너리

    Returns:
        추론된 저널명 또는 출판사명 (폴백)
    """
    # sender로 출판사 찾기 (Display Name <email> 형식 대응, 배열 지원)
    matched_publisher = None
    sender_lower = sender.lower()
    for pub_key, pub_data in publishers.items():
        senders = pub_data.get("sender", [])
        if isinstance(senders, str):
            senders = [senders]
        if any(s.lower() in sender_lower for s in senders):
            matched_publisher = pub_data
            break

    if not matched_publisher:
        # 발신자 매칭 실패 시 빈 문자열 반환
        return ""

    # subject에서 저널명 탐색 (대소문자 구분 없이 검색)
    # 긴 이름 먼저 매칭 (예: "Science Advances"가 "Science"보다 우선)
    journals_sorted = sorted(
        matched_publisher.get("journals", []),
        key=len, reverse=True
    )
    for journal_name in journals_sorted:
        if journal_name.lower() in subject.lower():
            return journal_name

    # 저널명 매칭 실패 → From 헤더의 Display Name에서 추출 시도 (폴백 1)
    # 예: "Advanced Energy Materials <WileyOnlineLibrary@wiley.com>" → "Advanced Energy Materials"
    import re
    display_match = re.match(r'^(.+?)\s*<', sender)
    if display_match:
        display_name = display_match.group(1).strip().strip('"')
        # display name이 출판사명과 다르면 저널명일 가능성 높음
        if display_name != matched_publisher.get("name", ""):
            return display_name

    # 최종 폴백 → 출판사명 반환
    return matched_publisher.get("name", "")
