"""Gmail API로 출판사별 ASAP 메일 HTML을 수집하여 tests/fixtures/에 저장하는 스크립트

용도: 파서 구현(Plan 02)의 선행 조건인 실제 메일 HTML fixture 확보
실행: python collect_samples.py
"""
import json
import os
import sys

from auth import get_gmail_service
from gmail_client import extract_body


def save_fixture(publisher_key: str, sender: str, max_count: int = 2) -> list[str]:
    """Gmail에서 특정 발신자의 메일 HTML을 tests/fixtures/에 저장한다.

    Args:
        publisher_key: 출판사 키 (파일명 접두사에 사용, 예: "acs")
        sender: 검색할 발신자 이메일 주소
        max_count: 최대 수집 메일 수 (기본값: 2)

    Returns:
        저장된 파일 경로 목록
    """
    service = get_gmail_service()
    saved_paths = []

    # Gmail 검색: 특정 발신자 메일 검색
    response = service.users().messages().list(
        userId="me",
        q=f"from:{sender}",
        maxResults=max_count
    ).execute()

    messages = response.get("messages", [])
    if not messages:
        print(f"[경고] {publisher_key} ({sender}): 메일 검색 결과 없음")
        return saved_paths

    for idx, msg in enumerate(messages, start=1):
        # 전체 메일 payload 가져오기
        full_msg = service.users().messages().get(
            userId="me",
            id=msg["id"],
            format="full"
        ).execute()

        # 실제 From 헤더 추출 (sender 검증용)
        headers = full_msg.get("payload", {}).get("headers", [])
        from_header = next(
            (h["value"] for h in headers if h["name"] == "From"),
            "(알 수 없음)"
        )
        print(f"  [{publisher_key}] 메일 {idx}: From = {from_header}")

        # HTML 본문 추출 (base64url 디코딩 포함)
        html_body = extract_body(full_msg["payload"])

        if not html_body:
            print(f"  [{publisher_key}] 메일 {idx}: HTML 본문 없음, 건너뜀")
            continue

        # tests/fixtures/{publisher_key}_{idx:02d}.html로 저장
        fixture_path = os.path.join("tests", "fixtures", f"{publisher_key}_{idx:02d}.html")
        with open(fixture_path, "w", encoding="utf-8") as f:
            f.write(html_body)

        file_size = os.path.getsize(fixture_path)
        print(f"  [{publisher_key}] 저장 완료: {fixture_path} ({file_size:,} bytes)")
        saved_paths.append(fixture_path)

    return saved_paths


def verify_senders(service, publishers: dict) -> dict:
    """publishers.json의 sender 이메일과 실제 Gmail 발신자를 비교 검증한다.

    각 출판사의 sender 이메일로 Gmail을 검색하고, 실제 메일의 From 헤더를 추출하여
    publishers.json 값과 불일치 여부를 확인한다.

    Args:
        service: Gmail API 서비스 객체 (auth.py의 get_gmail_service() 반환값)
        publishers: 출판사 설정 딕셔너리 (publishers.json 로드 결과)

    Returns:
        불일치 항목 딕셔너리: {publisher_key: {"expected": sender, "actual": real_sender}}
        모두 일치하거나 메일이 없으면 빈 dict 반환
    """
    mismatches = {}

    for key, pub_data in publishers.items():
        sender = pub_data.get("sender", "")
        if not sender:
            continue

        # 발신자로 최근 메일 1건 검색
        response = service.users().messages().list(
            userId="me",
            q=f"from:{sender}",
            maxResults=1
        ).execute()

        messages = response.get("messages", [])
        if not messages:
            print(f"[검증] {key}: 메일 없음 (발신자: {sender})")
            continue

        # 실제 From 헤더 추출
        full_msg = service.users().messages().get(
            userId="me",
            id=messages[0]["id"],
            format="full"
        ).execute()

        headers = full_msg.get("payload", {}).get("headers", [])
        real_from = next(
            (h["value"] for h in headers if h["name"] == "From"),
            ""
        )

        # From 헤더에서 이메일 주소만 추출 (예: "ACS <alerts@acs.org>" → "alerts@acs.org")
        real_email = real_from
        if "<" in real_from and ">" in real_from:
            real_email = real_from.split("<")[1].rstrip(">").strip()

        # 발신자와 비교 (대소문자 무시)
        if real_email.lower() != sender.lower():
            print(f"[불일치] {key}: 설정={sender} / 실제={real_email}")
            mismatches[key] = {"expected": sender, "actual": real_email}
        else:
            print(f"[일치] {key}: {sender}")

    return mismatches


def main():
    """메인 함수: 발신자 검증 후 각 출판사 메일 fixture 수집 및 저장"""
    # publishers.json 로드
    publishers_path = "publishers.json"
    with open(publishers_path, "r", encoding="utf-8") as f:
        publishers = json.load(f)

    print("=== Gmail 인증 ===")
    service = get_gmail_service()
    print("인증 완료\n")

    print("=== 발신자 검증 ===")
    mismatches = verify_senders(service, publishers)

    # 불일치 발견 시 publishers.json 자동 수정
    if mismatches:
        print(f"\n발신자 불일치 {len(mismatches)}건 발견 → publishers.json 자동 수정")
        for key, diff in mismatches.items():
            old_sender = diff["expected"]
            new_sender = diff["actual"]
            publishers[key]["sender"] = new_sender
            print(f"  [수정] {key}: {old_sender} -> {new_sender}")

        # 수정된 publishers.json 저장
        with open(publishers_path, "w", encoding="utf-8") as f:
            json.dump(publishers, f, ensure_ascii=False, indent=2)
        print(f"publishers.json 저장 완료\n")
    else:
        print("발신자 모두 일치\n")

    # tests/fixtures/ 디렉토리 생성
    os.makedirs(os.path.join("tests", "fixtures"), exist_ok=True)

    print("=== fixture 수집 ===")
    total_files = 0
    total_bytes = 0

    for key, pub_data in publishers.items():
        sender = pub_data.get("sender", "")
        print(f"\n[{key}] {pub_data.get('name', '')} ({sender})")
        saved = save_fixture(key, sender, max_count=2)
        total_files += len(saved)
        for path in saved:
            total_bytes += os.path.getsize(path)

    print(f"\n=== 수집 완료 ===")
    print(f"총 {total_files}개 파일 저장 ({total_bytes:,} bytes)")


if __name__ == "__main__":
    main()
