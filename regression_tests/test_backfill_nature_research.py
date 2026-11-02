"""backfill_nature_research.py(Nature Research 섹션 누락분 백필) 회귀 테스트 - 가짜 Gmail 클라이언트와 임시 DB만 쓴다.

검증: Gmail은 list/get만(그 밖의 호출은 즉시 실패) / 드라이런은 DB 무변경 + 저널별 신규 건수 + 오기 저널 의심 목록 /
논문별 규칙(제외 저널·발행일)은 main.run_pipeline과 같음 / --apply는 백업 선행 후 신규만 저장하고 영향받은 월 CSV 생성 /
재실행 안전 / 오기 저널 행은 건드리지 않음.
"""
import base64
import datetime as dt
import os

import httplib2
import pytest
from googleapiclient.errors import HttpError

import backfill_nature_research as bnr
import store
from models import PaperMetadata

NATURE_SENDER = "Nature Portfolio eAlerts <ealert@nature.com>"
ALERT_SENDER = "Nature <alerts@nature.com>"

# 합성 논문 제목 (4단어 이상)
C1, C2, C3 = ("Synthetic catalytic result number one", "Synthetic catalytic result number two", "Synthetic catalytic result number three")
E1, E2, E3 = ("Synthetic energy result number one", "Synthetic energy result number two", "Synthetic energy review number three")
M1, M2 = ("Synthetic methods article number one", "Synthetic methods article number two")
X = "Synthetic shared article appearing in both alerts"
N1 = "Synthetic flagship article number one"
O1 = "Synthetic catalytic result before the window"


def _html(*sections):
    body = ""
    for level, name, titles in sections:
        body += f"<{level}>{name}</{level}>" + "".join(
            f'<table><tr><td><div><a href="https://example.invalid/{i}">{t}</a></div></td></tr></table>'
            for i, t in enumerate(titles)
        )
    return f"<html><body><h1>Synthetic Contents</h1>{body}</body></html>"


def _message(msg_id, subject, html, received_utc, sender=NATURE_SENDER):
    millis = int(dt.datetime.fromisoformat(received_utc).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    data = base64.urlsafe_b64encode(html.encode()).decode().rstrip("=")
    return {
        "id": msg_id, "internalDate": str(millis),
        "payload": {"mimeType": "text/html", "body": {"data": data}, "headers": [
            {"name": "From", "value": sender}, {"name": "Subject", "value": subject},
        ]},
    }


class _Execute:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class _Messages:
    def __init__(self, service):
        self._service = service

    def list(self, **kwargs):
        self._service.list_calls.append(kwargs)
        return _Execute(lambda: {"messages": [{"id": i} for i in self._service.mails]})

    def get(self, **kwargs):
        self._service.get_ids.append(kwargs["id"])
        error = self._service.errors.get(kwargs["id"])

        def run():
            if error is not None:
                raise error
            return self._service.mails[kwargs["id"]]

        return _Execute(run)

    def __getattr__(self, name):  # modify/batchModify/... : 읽기 전용 스크립트가 부르면 안 된다
        raise AssertionError(f"Gmail 읽기 전용 위반: messages().{name}")


class FakeGmail:
    """list/get 만 지원하는 가짜 Gmail 서비스. 라벨 관련 호출은 즉시 실패한다."""

    def __init__(self, messages, errors=None):
        self.mails = {m["id"]: m for m in messages}
        self.errors = errors or {}
        self.list_calls, self.get_ids = [], []

    def users(self):
        return self

    def messages(self):
        return _Messages(self)

    def labels(self):
        raise AssertionError("Gmail 읽기 전용 위반: labels()")


def _mails():
    return [
        _message("cat", "Nature Catalysis Contents: Volume 9 Number 10",
                 _html(("h2", "News & Views", ["Synthetic news and views piece here"]), ("h2", "Research", [C1, C2, C3])),
                 "2026-10-23T09:00:00"),
        _message("eng", "Nature Energy Contents: Volume 11 Number 9",
                 _html(("h2", "Reviews", [E3]), ("h2", "Research", [E1, E2])), "2026-09-21T09:00:00"),
        _message("meth", "Nature Methods Contents: Volume 23 Number 10",
                 _html(("h2", "Articles", [M1, M2, X])), "2026-10-10T09:00:00"),
        _message("main", "Nature alert for 29th October 2026",
                 _html(("h2", "Research", []), ("h3", "Articles", [N1, X])), "2026-10-29T09:00:00", sender=ALERT_SENDER),
        _message("old", "Nature Catalysis Contents: Volume 9 Number 3",
                 _html(("h2", "Research", [O1])), "2026-03-15T09:00:00"),
        _message("other", "ACS alert", _html(("h2", "Research", ["Synthetic paper from some other sender"])),
                 "2026-10-23T09:00:00", sender="ACS Publications <updates@acspubs.org>"),
    ]


@pytest.fixture
def seeded(conn, monkeypatch):
    """DB: C1(저장됨) / C2(삭제 표시 행) / M1·X(저널이 'Nature'로 오기된 행) / Z(관계없는 'Nature' 행)."""
    def add(title, journal, date):
        store.save_papers([PaperMetadata(title=title, journal=journal, date=date)], conn, today=dt.date(2026, 10, 30))

    add(C1, "Nature Catalysis", "2026-10-23")
    add(C2, "Nature Catalysis", "2026-10-23")
    conn.execute("UPDATE papers SET deleted_at='2026-10-24T00:00:00Z' WHERE title=?", (C2,))
    add(M1, "Nature", "2026-10-10")
    add(X, "Nature", "2026-10-29")
    add("Synthetic unrelated flagship paper entry", "Nature", "2026-10-01")
    return conn


def _run(monkeypatch, argv, messages=None, errors=None):
    service = FakeGmail(messages if messages is not None else _mails(), errors)
    monkeypatch.setattr(bnr, "get_gmail_service", lambda: service)
    return service, bnr.main(argv)


def _journal_counts(conn):
    return dict(conn.execute("SELECT journal, COUNT(*) FROM papers GROUP BY journal").fetchall())


def test_dry_run_reports_new_papers_and_misattributed_rows_without_writing(seeded, monkeypatch, capsys, db_file):
    seq, counts = store.last_seq(seeded), _journal_counts(seeded)
    service, code = _run(monkeypatch, [])
    out = capsys.readouterr().out

    assert code == 0
    assert "드라이런" in out and "신규 논문 6건" in out
    # 저널별 신규: Catalysis는 C3 하나(C1 저장됨·C2 삭제행도 이미 점유), Energy 3(Research 2 + Reviews 1), Nature 본지 N1 하나,
    # Nature Methods는 M2 하나(M1·X는 'Nature'로 이미 DB에 있어 제목 키가 점유)
    assert "Nature Energy: 3건" in out and "Nature Catalysis: 1건" in out and "Nature: 1건" in out and "Nature Methods: 1건" in out
    assert C3 in out and N1 in out and E3 in out and C1 not in out
    # Nature Methods 알림 논문 M2는 'Nature Methods' 새 후보다. 오기 저널 행 M1은 제목 키가 이미 DB에 있어 새 후보가 아니다
    new_part = out.split("오기 저널 의심")[0]
    assert "[Nature Methods] " + M2[:40] in new_part and M1 not in new_part
    # 오기 저널 의심: Methods 알림에 실린 'Nature' 행 M1만. X는 진짜 Nature 알림에도 실려서, 관계없는 행·삭제행은 대상이 아니다
    mis = out.split("오기 저널 의심")[1]
    m1_id = seeded.execute("SELECT id FROM papers WHERE title=?", (M1,)).fetchone()[0]
    assert "1건" in mis.splitlines()[0] and f"id={m1_id}" in mis and M1[:40] in mis and "pub_date=2026-10-10" in mis
    assert X not in mis and "unrelated flagship" not in mis
    # DB 무변경, CSV 없음, 오기 행은 그대로
    assert store.last_seq(seeded) == seq and _journal_counts(seeded) == counts
    assert not os.path.exists(store.csv_path("2026-10"))
    assert seeded.execute("SELECT journal FROM papers WHERE title=?", (M1,)).fetchone()[0] == "Nature"


def test_gmail_is_only_listed_and_fetched_for_nature_senders_since_the_cutoff(seeded, monkeypatch):
    service, _ = _run(monkeypatch, ["--since", "2026-04-01"])  # 라벨·modify 호출은 FakeGmail 이 AssertionError 로 막는다
    (call,) = service.list_calls
    after = int(dt.datetime(2026, 4, 1, tzinfo=dt.timezone.utc).timestamp())
    assert call["userId"] == "me" and call["includeSpamTrash"] is True
    assert "from:ealert@nature.com" in call["q"] and "from:alerts@nature.com" in call["q"] and f"after:{after}" in call["q"]
    assert "label:" not in call["q"]  # 이미 처리 라벨이 붙은 메일이 대상이다
    assert sorted(service.get_ids) == sorted(m["id"] for m in _mails())


def test_since_filters_by_utc_received_date(seeded, monkeypatch, capsys):
    _run(monkeypatch, ["--since", "2026-10-01"])
    out = capsys.readouterr().out
    assert "신규 논문 3건" in out and C3 in out and N1 in out and M2 in out and E1 not in out and O1 not in out


def test_apply_backs_up_first_saves_only_new_papers_and_exports_affected_months(seeded, monkeypatch, capsys):
    seq_before = store.last_seq(seeded)
    backups = []
    monkeypatch.setattr(bnr.backup_db, "make_backup", lambda: backups.append(store.last_seq(seeded)) or "backup.db.gz")
    service, code = _run(monkeypatch, ["--apply"])
    out = capsys.readouterr().out

    assert code == 0 and backups == [seq_before]  # 백업은 저장 전에
    assert "적용 결과" in out and "저장 6건" in out
    rows = {r["title"]: r for r in seeded.execute("SELECT * FROM papers WHERE deleted_at IS NULL")}
    assert {t: rows[t]["journal"] for t in (C3, E1, E2, E3, N1, M2)} == {
        C3: "Nature Catalysis", E1: "Nature Energy", E2: "Nature Energy", E3: "Nature Energy", N1: "Nature", M2: "Nature Methods",
    }
    # 발행일 규칙: 파서가 날짜를 주지 않으므로 메일 수신일(UTC), bucket 은 발행월
    assert (rows[C3]["pub_date"], rows[C3]["bucket_month"]) == ("2026-10-23", "2026-10")
    assert (rows[E1]["pub_date"], rows[E1]["bucket_month"]) == ("2026-09-21", "2026-09")
    assert (rows[M2]["pub_date"], rows[M2]["bucket_month"]) == ("2026-10-10", "2026-10")
    assert all(rows[t]["status"] == "대기중" and rows[t]["status_source"] == "get_asap" for t in (C3, E1, E2, E3, N1, M2))
    # 기간 밖·타 발신자·이미 있는 논문은 저장되지 않는다
    assert not {O1, "Synthetic paper from some other sender"} & set(rows)
    assert _journal_counts(seeded)["Nature Methods"] == 1
    # 오기 저널 행은 건드리지 않는다(삭제·정정은 사용자가 따로 결정)
    assert rows[M1]["journal"] == "Nature" and rows[X]["journal"] == "Nature"
    # 영향받은 두 달의 CSV 가 생겼다
    for month in ("2026-09", "2026-10"):
        text = open(store.csv_path(month), encoding="utf-8-sig").read()
        assert (E1 in text) == (month == "2026-09") and (C3 in text) == (month == "2026-10")


def test_apply_is_idempotent_and_skips_backup_when_nothing_is_new(seeded, monkeypatch, capsys):
    monkeypatch.setattr(bnr.backup_db, "make_backup", lambda: "backup.db.gz")
    assert _run(monkeypatch, ["--apply"])[1] == 0
    seq = store.last_seq(seeded)
    capsys.readouterr()

    monkeypatch.setattr(bnr.backup_db, "make_backup", lambda: pytest.fail("신규가 없으면 백업하지 않는다"))
    assert _run(monkeypatch, ["--apply"])[1] == 0
    assert "신규 논문 0건" in capsys.readouterr().out and store.last_seq(seeded) == seq


@pytest.mark.parametrize("skip_backup", [False, True])
def test_skip_backup_flag(seeded, monkeypatch, skip_backup):
    calls = []
    monkeypatch.setattr(bnr.backup_db, "make_backup", lambda: calls.append(1) or "backup.db.gz")
    assert _run(monkeypatch, ["--apply"] + (["--skip-backup"] if skip_backup else []))[1] == 0
    assert calls == ([] if skip_backup else [1])


def test_misattributed_row_is_listed_but_never_saved_again_as_a_nature_methods_duplicate(seeded, monkeypatch):
    """M1은 'Nature'로 오기된 채 DB에 있고 Methods 알림에도 실린다 - 오기 목록에만 오르고 새 후보·중복 행이 되지 않는다."""
    monkeypatch.setattr(bnr.backup_db, "make_backup", lambda: "backup.db.gz")
    plan = bnr.collect(FakeGmail(_mails()), bnr.pipeline._load_publishers(), dt.date(2026, 4, 1), seeded)
    assert [r["title"] for r in plan["misattributed"]] == [M1]
    assert M1 not in {paper.title for _, paper in plan["new"]} and plan["stats"]["already_in_db"] >= 2  # M1·X
    assert M2 in {paper.title for _, paper in plan["new"]}

    assert _run(monkeypatch, ["--apply"])[1] == 0
    rows = seeded.execute("SELECT journal FROM papers WHERE title=?", (M1,)).fetchall()
    assert [r["journal"] for r in rows] == ["Nature"]  # 'Nature Methods' 두 번째 행이 생기지 않는다


def test_excluded_journal_rule_still_applies_to_alert_papers(seeded, monkeypatch, capsys):
    monkeypatch.setattr(bnr.pipeline, "_EXCLUDED_JOURNALS", {"nature energy"})
    _run(monkeypatch, [])
    out = capsys.readouterr().out
    assert "신규 논문 3건" in out and "Nature Energy" not in out.split("오기 저널 의심")[0].split("저널별:")[1]
    assert E1 not in out and M2 in out


def test_future_dated_paper_uses_the_same_clamp_as_the_pipeline(conn, monkeypatch):
    class FutureParser:
        def parse(self, body):
            return [PaperMetadata(title="Synthetic future dated research paper", journal="", date="2026-12-31")]

    monkeypatch.setattr(bnr, "NatureParser", FutureParser)
    mail = _message("cat", "Nature Catalysis Contents: Volume 9 Number 10", "<html></html>", "2026-10-23T09:00:00")
    assert _run(monkeypatch, ["--apply", "--skip-backup"], [mail])[1] == 0
    row = conn.execute("SELECT journal, pub_date FROM papers").fetchone()
    assert (row["journal"], row["pub_date"]) == ("Nature Catalysis", "2026-10-23")


def _http_error(status):
    content = b'{"error": {"code": %d, "message": "synthetic"}}' % status
    return HttpError(httplib2.Response({"status": status}), content)


def test_deleted_message_is_counted_and_not_fatal(seeded, monkeypatch, capsys):
    _, code = _run(monkeypatch, [], errors={"eng": _http_error(404)})
    out = capsys.readouterr().out
    assert code == 0 and "삭제됨 1" in out and "읽기/파싱 실패 0" in out
    assert E1 not in out  # 읽지 못한 메일의 논문은 후보에 없다 (다음 실행에서 다시 시도)


def test_unreadable_message_makes_the_exit_code_nonzero_but_other_mails_still_apply(seeded, monkeypatch, capsys):
    _, code = _run(monkeypatch, ["--apply", "--skip-backup"], errors={"cat": _http_error(500)})
    out = capsys.readouterr().out
    assert code == 1 and "읽기/파싱 실패 1" in out
    assert seeded.execute("SELECT COUNT(*) FROM papers WHERE title=?", (E1,)).fetchone()[0] == 1
    assert seeded.execute("SELECT COUNT(*) FROM papers WHERE title=?", (C3,)).fetchone()[0] == 0  # 재실행하면 채워진다


def test_missing_db_fails_cleanly(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "nowhere" / "asap.db"))
    monkeypatch.setattr(bnr, "get_gmail_service", lambda: pytest.fail("DB가 없으면 Gmail에 접속하지 않는다"))
    assert bnr.main([]) == 1
    assert "정본 DB가 없습니다" in capsys.readouterr().err
