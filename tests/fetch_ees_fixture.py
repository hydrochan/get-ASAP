"""Gmail에서 RSC / EES 메일을 검색하여 fixture 파일로 저장.

사용: python -m tests.fetch_ees_fixture
"""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from gmail_client import extract_body  # type: ignore


def get_gmail_service_no_refresh():
    """token.json의 access_token을 그대로 사용 (refresh 우회).

    Windows requests SSL 문제로 refresh가 실패하므로,
    get_token_curl.py 직후 1시간 내 실행 시 refresh 없이 동작.
    """
    token_path = Path(__file__).resolve().parents[1] / "token.json"
    with open(token_path) as f:
        data = json.load(f)
    creds = Credentials(
        token=data["token"],
        refresh_token=data.get("refresh_token"),
        token_uri=data["token_uri"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        scopes=data["scopes"],
    )
    # expiry를 미래로 강제 세팅 → valid=True, refresh 시도 안 함
    creds.expiry = datetime.utcnow() + timedelta(minutes=55)
    return build("gmail", "v1", credentials=creds)

FIXTURE_DIR = Path(__file__).parent / "fixtures"
FIXTURE_DIR.mkdir(exist_ok=True)


def header(msg, name):
    for h in msg.get("payload", {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def main():
    service = get_gmail_service_no_refresh()

    # 1) rsc.org로부터 온 최근 메일 모두 조회
    resp = service.users().messages().list(
        userId="me", q="from:rsc.org", maxResults=50
    ).execute()
    msgs = resp.get("messages", [])
    print(f"[INFO] rsc.org로부터 온 메일: {len(msgs)}건")

    senders_seen = {}  # sender -> [(subject, id)]
    ees_candidate = None

    for m in msgs:
        full = service.users().messages().get(
            userId="me", id=m["id"], format="full"
        ).execute()
        sender = header(full, "From")
        subject = header(full, "Subject")
        senders_seen.setdefault(sender, []).append((subject, m["id"]))

        if ees_candidate is None and "energy" in subject.lower() and "environ" in subject.lower():
            ees_candidate = (m["id"], sender, subject, full)

    print("\n=== sender별 집계 ===")
    for s, items in senders_seen.items():
        print(f"  [{len(items)}] {s}")
        for subj, _id in items[:3]:
            print(f"      - {subj[:80]}")

    if ees_candidate is None:
        print("\n[WARN] EES 메일을 찾지 못함. rsc.org 전체 메일 중 최신 1건을 fixture로 저장.")
        if not msgs:
            print("[ERROR] rsc.org 메일이 아예 없음.")
            return
        m0 = msgs[0]
        full = service.users().messages().get(
            userId="me", id=m0["id"], format="full"
        ).execute()
        ees_candidate = (m0["id"], header(full, "From"), header(full, "Subject"), full)

    mid, sender, subject, full = ees_candidate
    html = extract_body(full["payload"])
    print(f"\n[FOUND] id={mid}")
    print(f"        From: {sender}")
    print(f"        Subject: {subject}")
    print(f"        HTML size: {len(html)} bytes")

    out = FIXTURE_DIR / "rsc_ees_01.html"
    out.write_text(html, encoding="utf-8")
    print(f"[SAVED] {out}")


if __name__ == "__main__":
    main()
