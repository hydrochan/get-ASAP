"""Synthetic, credential-free retry regressions kept outside private email fixtures."""
import copy
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import httplib2
from googleapiclient.errors import HttpError

import main
import store

# 가짜로 바꾸기 전의 실제 함수(실제 SQLite와 엮는 통합 테스트용)
_REAL_STORE = SimpleNamespace(save_papers=store.save_papers, require_db=store.require_db)
_REAL_REFRESH = main._refresh_cache
_REAL_LOAD_PUBLISHERS = main._load_publishers
_REAL_IS_EXCLUDED = main._is_excluded_journal


def _http_error(status: int) -> HttpError:
    """Gmail API가 돌려주는 것과 같은 모양의 HttpError(404면 notFound 본문)."""
    reason = "notFound" if status == 404 else "backendError"
    content = ('{"error": {"code": %d, "message": "Requested entity was not found.", '
               '"errors": [{"reason": "%s"}]}}' % (status, reason)).encode()
    return HttpError(httplib2.Response({"status": status}), content)


@pytest.fixture
def pipeline(monkeypatch):
    import main

    h = SimpleNamespace(
        state={"historyId": "100"}, checkpoints=[], message_ids=["m1", "m2"],
        parse_failures=set(), save_failures=set(), label_failures=set(),
        saved_titles=set(), labels=[], paper_counts={}, store_exception=False,
        internal_dates={}, paper_dates={}, saved_dates={},  # 메일 internalDate·파서 날짜 주입, 저장된 날짜 기록
        get_errors={}, mail_headers={}, paper_journals={},  # 메일 get 오류(HttpError) 주입, 메일별 (From, Subject), 파서 저널
        saved_journals={},  # 저장된 논문의 저널 기록
    )
    service = MagicMock()
    h.service = service

    def history(**kwargs):
        messages = h.message_ids if kwargs["startHistoryId"] == "100" else []
        return SimpleNamespace(execute=lambda: {
            "historyId": "200", "history": [{"messagesAdded": [
                {"message": {"id": mid}} for mid in messages
            ]}],
        })

    service.users().history().list.side_effect = history
    def get_message(**kw):
        mid = kw["id"]

        def execute():
            if mid in h.get_errors:
                raise h.get_errors[mid]
            sender, subject = h.mail_headers.get(mid, ("alerts@example.invalid", "Synthetic journal"))
            return {"payload": {"body_id": mid, "headers": [
                {"name": "From", "value": sender},
                {"name": "Subject", "value": subject},
            ]}, **({"internalDate": h.internal_dates[mid]} if mid in h.internal_dates else {})}

        return SimpleNamespace(execute=execute)

    service.users().messages().get.side_effect = get_message

    def parse(mid):
        if mid in h.parse_failures:
            raise ValueError("Synthetic parser error")
        return [SimpleNamespace(
            title=f"{mid}-{i}", journal=h.paper_journals.get(mid, "Synthetic journal"),
            date=h.paper_dates.get(mid, "2026-09-07"), url="",
        ) for i in range(h.paper_counts.get(mid, 1))]

    def save_papers(papers):
        if h.store_exception:
            raise RuntimeError("Synthetic store exception")
        result = {"saved": 0, "skipped": 0, "failed": 0}
        for paper in papers:
            h.saved_dates[paper.title] = paper.date
            h.saved_journals[paper.title] = paper.journal
            if paper.title in h.save_failures:
                result["failed"] += 1
            elif paper.title in h.saved_titles:
                result["skipped"] += 1
            else:
                h.saved_titles.add(paper.title)
                result["saved"] += 1
        return result

    def save_state(state):
        h.checkpoints.append(copy.deepcopy(state))
        h.state = copy.deepcopy(state)

    def mark_processed(_service, mid, _label):
        if mid in h.label_failures:
            raise RuntimeError("Synthetic label error")
        h.labels.append(mid)

    monkeypatch.setattr(main, "_load_publishers", lambda: {
        "synthetic": {"name": "Synthetic", "sender": "alerts@example.invalid"},
    })
    monkeypatch.setattr(main, "get_gmail_service", lambda: service)
    monkeypatch.setattr(main, "load_state", lambda: copy.deepcopy(h.state))
    monkeypatch.setattr(main, "save_state", save_state)
    monkeypatch.setattr(main, "get_or_create_label", lambda _: "synthetic-label")
    monkeypatch.setattr(main, "load_parsers", lambda: [SimpleNamespace(publisher_name="Synthetic", parse=parse)])
    monkeypatch.setattr(main, "extract_body", lambda payload: payload["body_id"])
    monkeypatch.setattr(main, "_is_excluded_journal", lambda _: False)
    monkeypatch.setattr(main.store, "require_db", lambda: None)
    monkeypatch.setattr(main.store, "save_papers", save_papers)
    monkeypatch.setattr(main, "mark_processed", mark_processed)
    monkeypatch.setattr(main, "_refresh_cache", lambda: None)
    h.run = main.run_pipeline
    return h


def test_partial_message_save_retries_only_unfinished_message(pipeline):
    h = pipeline
    h.paper_counts["m1"] = 2
    h.save_failures.add("m1-1")
    first = h.run()
    assert h.checkpoints[0] == {"historyId": "200", "pendingMessageIds": ["m1", "m2"]}
    assert first["saved"] == 2 and first["failed"] == 1
    assert first["failed_messages"] == 1
    assert h.labels == ["m2"]
    assert h.state["pendingMessageIds"] == ["m1"]

    h.save_failures.clear()
    second = h.run()
    assert second["saved"] == second["skipped"] == 1
    assert second["failed_messages"] == 0
    assert h.labels == ["m2", "m1"]
    assert h.state["pendingMessageIds"] == []


@pytest.mark.parametrize("failure", ["parse_failures", "label_failures"])
def test_message_failure_survives_cursor_advance_and_recovers(pipeline, failure):
    h = pipeline
    getattr(h, failure).add("m1")
    assert h.run()["failed_messages"] == 1
    assert h.state["historyId"] == "200"
    assert h.state["pendingMessageIds"] == ["m1"]
    assert h.labels == ["m2"]
    getattr(h, failure).clear()
    assert h.run()["failed_messages"] == 0
    assert h.state["pendingMessageIds"] == []
    assert set(h.saved_titles) == {"m1-0", "m2-0"}


@pytest.mark.parametrize("messages", [[], ["m1"]])
def test_dry_run_keeps_cursor_and_retry_queue_unchanged(pipeline, messages):
    h = pipeline
    h.message_ids = messages
    h.state["pendingMessageIds"] = ["pending"]
    before = copy.deepcopy(h.state)
    h.run(dry_run=True)
    assert h.state == before
    assert h.checkpoints == h.labels == []
    assert h.saved_titles == set()
    h.run()
    assert h.state["pendingMessageIds"] == []
    assert set(h.labels) == set(["pending", *messages])


def test_store_exception_keeps_all_messages_pending(pipeline):
    h = pipeline
    h.store_exception = True
    assert h.run()["failed_messages"] == 2
    assert h.state["pendingMessageIds"] == ["m1", "m2"]
    assert h.labels == []
    h.store_exception = False
    assert h.run()["saved"] == 2
    assert h.state["pendingMessageIds"] == []


def test_empty_dry_run_never_saves_state(pipeline):
    pipeline.message_ids = []
    pipeline.run(dry_run=True)
    assert pipeline.state == {"historyId": "100"}
    assert pipeline.checkpoints == []


def test_fatal_failure_after_checkpoint_cannot_lose_messages(pipeline, monkeypatch):
    import main
    h = pipeline
    monkeypatch.setattr(main.store, "require_db", lambda: (_ for _ in ()).throw(RuntimeError("Synthetic crash")))
    with pytest.raises(RuntimeError, match="Synthetic crash"):
        h.run()
    assert h.state == {"historyId": "200", "pendingMessageIds": ["m1", "m2"]}
    monkeypatch.setattr(main.store, "require_db", lambda: None)
    assert h.run()["saved"] == 2
    assert h.state["pendingMessageIds"] == []


def test_state_replace_failure_preserves_previous_checkpoint(tmp_path, monkeypatch):
    import gmail_client
    path = tmp_path / "state.json"
    gmail_client.save_state({"historyId": "100"}, str(path))
    old_content = path.read_bytes()
    monkeypatch.setattr(gmail_client.os, "replace", lambda *_: (_ for _ in ()).throw(OSError("Synthetic disk failure")))
    with pytest.raises(OSError, match="Synthetic disk failure"):
        gmail_client.save_state({"historyId": "200", "pendingMessageIds": ["m1"]}, str(path))
    assert path.read_bytes() == old_content
    assert list(tmp_path.glob(".state-*.tmp")) == []


def test_interruption_after_external_writes_preserves_retry_checkpoint(pipeline, monkeypatch):
    import main
    h = pipeline

    def interrupt_refresh():
        raise KeyboardInterrupt("Synthetic interruption during cache refresh")

    monkeypatch.setattr(main, "_refresh_cache", interrupt_refresh)
    with pytest.raises(KeyboardInterrupt):
        h.run()
    assert h.labels == ["m1", "m2"]
    assert h.state["pendingMessageIds"] == ["m1", "m2"]
    monkeypatch.setattr(main, "_refresh_cache", lambda: None)
    result = h.run()
    assert result["saved"] == 0 and result["skipped"] == 2
    assert h.state["pendingMessageIds"] == []


# ---------- 날짜 없는 논문: 메일 수신일(internalDate, UTC)로 채움 ----------

def _internal_date(iso_utc: str) -> str:
    """UTC ISO 시각을 Gmail internalDate 형식(epoch 밀리초 문자열)으로 만든다."""
    return str(int(datetime.fromisoformat(iso_utc).replace(tzinfo=timezone.utc).timestamp() * 1000))


@pytest.mark.parametrize("received_utc, expected", [
    ("2026-10-22T23:30:00", "2026-10-22"),  # KST로는 10-23이지만 UTC 기준 날짜를 쓴다
    ("2026-10-23T00:00:00", "2026-10-23"),  # UTC 자정 경계
    ("2026-10-22T09:15:30", "2026-10-22"),
])
def test_dateless_paper_gets_mail_received_date_in_utc(pipeline, received_utc, expected):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = ""
    h.internal_dates["m1"] = _internal_date(received_utc)
    assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == expected


def test_parsed_date_is_kept_over_mail_received_date(pipeline):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = "2026-08-30"
    h.internal_dates["m1"] = _internal_date("2026-10-22T23:30:00")
    assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == "2026-08-30"


# ---------- 발행일이 메일 수신일보다 미래: 수신일로 대체 (ACS가 2026-12-31을 기재한 사고) ----------

def test_future_paper_date_is_clamped_to_mail_received_date(pipeline, caplog):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = "2026-12-31"
    h.internal_dates["m1"] = _internal_date("2026-10-29T03:00:00")
    with caplog.at_level("INFO", logger="main"):
        assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == "2026-10-29"
    msg = next(r.getMessage() for r in caplog.records if "미래" in r.getMessage())
    assert "2026-12-31" in msg and "2026-10-29" in msg and "m1-0" in msg


def test_past_and_same_day_paper_dates_are_kept(pipeline, caplog):
    h = pipeline
    h.message_ids = ["m1", "m2"]
    h.paper_dates.update({"m1": "2026-10-28", "m2": "2026-10-29"})
    h.internal_dates.update({k: _internal_date("2026-10-29T03:00:00") for k in ("m1", "m2")})
    with caplog.at_level("INFO", logger="main"):
        assert h.run()["saved"] == 2
    assert (h.saved_dates["m1-0"], h.saved_dates["m2-0"]) == ("2026-10-28", "2026-10-29")
    assert not any("미래" in r.getMessage() for r in caplog.records)


def test_future_date_beyond_utc_mail_date_is_clamped_at_the_boundary(pipeline):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = "2026-10-23"  # KST로는 수신일(10-23)이지만 UTC 수신일은 10-22
    h.internal_dates["m1"] = _internal_date("2026-10-22T23:30:00")
    assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == "2026-10-22"


def test_future_date_without_valid_internal_date_is_clamped_to_today(pipeline, monkeypatch):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = "2026-12-31"
    monkeypatch.setattr(main, "date", SimpleNamespace(today=lambda: date(2026, 10, 23)))
    assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == "2026-10-23"


@pytest.mark.parametrize("paper_date, mail_date, expected", [
    ("2026-12-31", "2026-10-29", "2026-10-29"),  # 미래 -> 수신일
    ("2026-10-30", "2026-10-29", "2026-10-29"),  # 하루 뒤도 미래
    ("2026-10-29", "2026-10-29", "2026-10-29"),  # 같은 날 유지
    ("2026-08-07", "2026-10-29", "2026-08-07"),  # 과거 유지
    ("", "2026-10-29", "2026-10-29"),            # 누락 -> 수신일
    (None, "2026-10-29", "2026-10-29"),
    ("2027-01-01T00:00:00", "2026-10-29", "2026-10-29"),  # 시각이 붙어도 앞 10자로 비교
    ("2026-10-29T23:59:59", "2026-10-29", "2026-10-29T23:59:59"),  # 같은 날이면 원문 그대로 유지
    ("2026-02-30", "2026-10-29", "2026-02-30"),  # 달력에 없는 날짜: 해석 불가라 건드리지 않음
    ("garbage", "2026-10-29", "garbage"),
    ("2026-8-7", "2026-10-29", "2026-8-7"),  # 0 패딩 없는 형식: 해석 불가라 건드리지 않음
    ("2026-12-31", "not-a-date", "2026-12-31"),  # 수신일을 못 믿으면 건드리지 않음
])
def test_effective_pub_date_rule(paper_date, mail_date, expected):
    assert main._effective_pub_date(paper_date, mail_date) == expected


_MISSING = object()


@pytest.mark.parametrize("internal_date", [_MISSING, "not-a-number", "", None, "9" * 30])
def test_dateless_paper_falls_back_to_today_without_valid_internal_date(pipeline, monkeypatch, internal_date):
    h = pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = ""
    if internal_date is not _MISSING:
        h.internal_dates["m1"] = internal_date
    monkeypatch.setattr(main, "date", SimpleNamespace(today=lambda: date(2026, 10, 23)))
    assert h.run()["saved"] == 1
    assert h.saved_dates["m1-0"] == "2026-10-23"


# ---------- Gmail 404(삭제된 메일): 영구 실패라 대기열에서 빼고 실행 실패로 세지 않는다 ----------

def test_deleted_message_404_is_dropped_from_pending_without_failing_the_run(pipeline, caplog):
    h = pipeline
    h.state["pendingMessageIds"] = ["gone"]  # 10-12부터 대기열에 남아 매 실행을 실패로 만든 메일
    h.get_errors["gone"] = _http_error(404)
    with caplog.at_level("WARNING", logger="main"):
        result = h.run()
    assert result["failed_messages"] == 0 and result["failed"] == 0  # main()의 종료 코드 판정 입력이 모두 0
    assert h.state["pendingMessageIds"] == []
    assert h.labels == ["m1", "m2"] and h.saved_titles == {"m1-0", "m2-0"}  # 다른 메일은 정상 처리
    assert [r.levelname for r in caplog.records if "gone" in r.getMessage()] == ["WARNING"]  # 경고는 딱 한 번

    calls_before = h.service.users().messages().get.call_count
    h.message_ids = []
    assert h.run()["failed_messages"] == 0
    assert h.service.users().messages().get.call_count == calls_before  # 다음 실행은 그 메일을 다시 요청하지 않는다


def test_deleted_message_in_dry_run_is_not_a_failure_and_keeps_state(pipeline):
    h = pipeline
    h.state["pendingMessageIds"] = ["gone"]
    h.get_errors["gone"] = _http_error(404)
    before = copy.deepcopy(h.state)
    assert h.run(dry_run=True)["failed_messages"] == 0
    assert h.state == before and h.checkpoints == []


def test_other_gmail_errors_stay_pending_and_fail_the_run(pipeline):
    h = pipeline
    h.get_errors["m1"] = _http_error(500)  # 일시적 서버 오류는 영구 실패가 아니다
    assert h.run()["failed_messages"] == 1
    assert h.state["pendingMessageIds"] == ["m1"]
    del h.get_errors["m1"]
    assert h.run()["failed_messages"] == 0 and h.state["pendingMessageIds"] == []


def test_is_message_not_found_only_matches_http_404():
    from gmail_client import is_message_not_found

    assert is_message_not_found(_http_error(404)) is True
    assert is_message_not_found(_http_error(500)) is False
    assert is_message_not_found(ValueError("404")) is False


# ---------- Nature Methods: 저널 추론이 'Nature'로 삼키지 않아야 제외 저널로 걸러진다 ----------

NATURE_SENDER = "Nature Portfolio eAlerts <ealert@nature.com>"


@pytest.mark.parametrize("subject, expected", [
    ("Nature Methods Contents: Volume 23 Number 10", "Nature Methods"),
    ("Nature Catalysis Contents: Volume 9 Number 10", "Nature Catalysis"),
    ("Nature alert for 29th October 2026", "Nature"),
])
def test_nature_alert_subject_infers_the_longest_matching_journal(subject, expected):
    from gmail_client import infer_journal

    assert infer_journal(NATURE_SENDER, subject, _REAL_LOAD_PUBLISHERS()) == expected


def test_every_excluded_nature_journal_is_listed_in_publishers():
    """제외 목록에만 있고 publishers.json엔 없으면 더 짧은 이름('Nature')으로 추론돼 제외가 작동하지 않는다."""
    nature_journals = {j.lower() for j in _REAL_LOAD_PUBLISHERS()["nature"]["journals"]}
    excluded_nature = {j for j in main._EXCLUDED_JOURNALS if j.startswith("nature ")}
    assert excluded_nature and excluded_nature <= nature_journals


def test_nature_methods_alert_is_skipped_as_excluded_while_others_are_saved(pipeline, monkeypatch):
    h = pipeline
    # fixture는 publishers와 제외 판정을 가짜로 바꿔 둔다 - 여기선 둘 다 실제(publishers.json, 제외 목록)를 쓴다
    monkeypatch.setattr(main, "_load_publishers", _REAL_LOAD_PUBLISHERS)
    monkeypatch.setattr(main, "_is_excluded_journal", _REAL_IS_EXCLUDED)
    synthetic_parse = main.load_parsers()[0].parse
    monkeypatch.setattr(main, "load_parsers", lambda: [SimpleNamespace(publisher_name="Nature", parse=synthetic_parse)])
    h.message_ids = ["methods", "catalysis"]
    h.paper_journals.update({"methods": "", "catalysis": ""})  # Nature 파서처럼 저널 미지정 -> 메일 제목으로 추론
    h.mail_headers.update({
        "methods": (NATURE_SENDER, "Nature Methods Contents: Volume 23 Number 10"),
        "catalysis": (NATURE_SENDER, "Nature Catalysis Contents: Volume 9 Number 10"),
    })
    result = h.run()
    assert result["saved"] == 1
    assert h.saved_journals == {"catalysis-0": "Nature Catalysis"}  # Methods 논문은 'Nature'로도 저장되지 않는다
    assert h.labels == ["methods", "catalysis"] and h.state["pendingMessageIds"] == []  # 메일 자체는 처리 완료


def _use_crossref_config(monkeypatch, tmp_path, content):
    """crossref_journals.json 대신 tmp 파일을 쓰게 하고 main 의 한 번 읽기 캐시를 비운다. content=None 이면 없는 파일."""
    import crossref_source

    path = tmp_path / "crossref_journals.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(crossref_source, "JOURNALS_PATH", str(path))
    monkeypatch.setattr(main, "_crossref_skip_cache", None)


def test_crossref_journal_email_papers_are_not_saved_while_normal_journals_are(pipeline, monkeypatch, tmp_path, caplog):
    h = pipeline
    _use_crossref_config(monkeypatch, tmp_path, (
        '{"skip_email_for_ingest": true, "journals": ['
        '{"journal": "Nature Communications", "mode": "ingest"},'
        '{"journal": "Nature Catalysis", "mode": "shadow"}]}'
    ))
    h.message_ids = ["crossref", "normal"]
    h.paper_journals.update({"crossref": "nature communications ", "normal": "Nature Catalysis"})  # 대소문자·공백 차이도 같은 저널
    with caplog.at_level("INFO", logger="main"):
        result = h.run()
    assert result["saved"] == 1
    assert h.saved_journals == {"normal-0": "Nature Catalysis"}  # shadow 저널은 그대로 저장
    assert h.labels == ["crossref", "normal"] and h.state["pendingMessageIds"] == []  # 메일 자체는 처리 완료
    assert any("Crossref 수집 저널이라 메일 경로 스킵" in r.getMessage() for r in caplog.records)


def test_crossref_skip_flag_off_skips_nothing(pipeline, monkeypatch, tmp_path):
    h = pipeline
    _use_crossref_config(monkeypatch, tmp_path, (
        '{"skip_email_for_ingest": false, "journals": [{"journal": "Nature Communications", "mode": "ingest"}]}'
    ))
    h.paper_journals["m1"] = "Nature Communications"
    assert h.run()["saved"] == 2


@pytest.mark.parametrize("content", [None, "{not json", '"just a string"', "[]"], ids=["missing", "invalid-json", "string", "list"])
def test_missing_or_broken_crossref_config_skips_nothing_and_warns(pipeline, monkeypatch, tmp_path, caplog, content):
    h = pipeline
    _use_crossref_config(monkeypatch, tmp_path, content)
    h.paper_journals["m1"] = "Nature Communications"
    with caplog.at_level("WARNING", logger="main"):
        result = h.run()
    assert result["saved"] == 2 and result["failed"] == 0  # 설정이 깨져도 파이프라인은 그대로 저장한다
    assert h.saved_journals["m1-0"] == "Nature Communications"
    warnings = [r for r in caplog.records if r.levelname == "WARNING" and "crossref_journals.json" in r.getMessage()]
    assert len(warnings) == 1  # 논문마다가 아니라 실행당 한 번만 읽고 한 번만 경고


# ---------- 실제 SQLite 정본과 엮은 통합 검증 (합성 메일) ----------

@pytest.fixture
def real_store_pipeline(pipeline, db_file, monkeypatch):
    """Gmail/파서는 가짜 그대로, 저장·DB 확인·CSV export만 실제 store 로 되돌린다(DB·cache는 tmp_path)."""
    import main

    monkeypatch.setattr(main.store, "require_db", _REAL_STORE.require_db)
    monkeypatch.setattr(main.store, "save_papers", _REAL_STORE.save_papers)
    monkeypatch.setattr(main, "_refresh_cache", _REAL_REFRESH)
    return pipeline


def test_real_store_end_to_end_second_run_saves_nothing(real_store_pipeline, db_file):
    h = real_store_pipeline
    h.paper_counts["m1"] = 2
    first = h.run()
    assert first["saved"] == 3 and first["failed"] == 0 and h.state["pendingMessageIds"] == [] and h.labels == ["m1", "m2"]
    conn = store.connect(str(db_file))
    try:
        assert conn.execute("SELECT COUNT(*) FROM papers").fetchone()[0] == 3
        assert {r[0] for r in conn.execute("SELECT bucket_month FROM papers")} == {"2026-09"}
        assert store.last_seq(conn) == 3
    finally:
        conn.close()
    csv_path = store.csv_path("2026-09")  # 저장 후 export_dirty 로 CSV 캐시가 만들어졌다
    assert os.path.exists(csv_path)
    text = open(csv_path, encoding="utf-8-sig").read()
    assert all(t in text for t in ("m1-0", "m1-1", "m2-0"))

    # 같은 메일을 다시 받아도(커서를 되돌려 재전달) 두 번째 실행의 saved 는 0, 전부 중복 스킵이고 seq 도 그대로
    h.state = {"historyId": "100"}
    second = h.run()
    assert second["saved"] == 0 and second["skipped"] == 3 and second["failed"] == 0
    conn = store.connect(str(db_file))
    try:
        assert store.last_seq(conn) == 3
    finally:
        conn.close()


def test_real_store_persists_clamped_future_date(real_store_pipeline, db_file):
    h = real_store_pipeline
    h.message_ids = ["m1"]
    h.paper_dates["m1"] = "2026-12-31"
    h.internal_dates["m1"] = _internal_date("2026-10-29T03:00:00")
    assert h.run()["saved"] == 1
    conn = store.connect(str(db_file))
    try:
        assert conn.execute("SELECT pub_date FROM papers WHERE title='m1-0'").fetchone()[0] == "2026-10-29"
    finally:
        conn.close()


def test_real_store_missing_db_fails_before_labels_and_keeps_messages_pending(pipeline, monkeypatch, tmp_path):
    import main

    monkeypatch.setenv("ASAP_DB_PATH", str(tmp_path / "missing" / "asap.db"))
    monkeypatch.setattr(main.store, "require_db", _REAL_STORE.require_db)
    with pytest.raises(FileNotFoundError):
        pipeline.run()
    assert pipeline.labels == [] and pipeline.state["pendingMessageIds"] == ["m1", "m2"]
    assert not (tmp_path / "missing").exists()  # 빈 DB를 만들지 않는다


def test_dry_run_never_opens_the_database(pipeline, monkeypatch, tmp_path, capsys):
    import main

    ghost = tmp_path / "ghost" / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(ghost))
    monkeypatch.setattr(main.store, "require_db", _REAL_STORE.require_db)
    monkeypatch.setattr(main.store, "save_papers", _REAL_STORE.save_papers)
    pipeline.run(dry_run=True)
    assert not ghost.exists() and "월 버킷: 2026-09" in capsys.readouterr().out
