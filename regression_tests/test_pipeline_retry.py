"""Synthetic, credential-free retry regressions kept outside private email fixtures."""
import copy
import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import main
import store

# 가짜로 바꾸기 전의 실제 함수(실제 SQLite와 엮는 통합 테스트용)
_REAL_STORE = SimpleNamespace(save_papers=store.save_papers, require_db=store.require_db)
_REAL_REFRESH = main._refresh_cache


@pytest.fixture
def pipeline(monkeypatch):
    import main

    h = SimpleNamespace(
        state={"historyId": "100"}, checkpoints=[], message_ids=["m1", "m2"],
        parse_failures=set(), save_failures=set(), label_failures=set(),
        saved_titles=set(), labels=[], paper_counts={}, store_exception=False,
    )
    service = MagicMock()

    def history(**kwargs):
        messages = h.message_ids if kwargs["startHistoryId"] == "100" else []
        return SimpleNamespace(execute=lambda: {
            "historyId": "200", "history": [{"messagesAdded": [
                {"message": {"id": mid}} for mid in messages
            ]}],
        })

    service.users().history().list.side_effect = history
    service.users().messages().get.side_effect = lambda **kw: SimpleNamespace(
        execute=lambda: {"payload": {"body_id": kw["id"], "headers": [
            {"name": "From", "value": "alerts@example.invalid"},
            {"name": "Subject", "value": "Synthetic journal"},
        ]}},
    )

    def parse(mid):
        if mid in h.parse_failures:
            raise ValueError("Synthetic parser error")
        return [SimpleNamespace(
            title=f"{mid}-{i}", journal="Synthetic journal", date="2026-09-07", url="",
        ) for i in range(h.paper_counts.get(mid, 1))]

    def save_papers(papers):
        if h.store_exception:
            raise RuntimeError("Synthetic store exception")
        result = {"saved": 0, "skipped": 0, "failed": 0}
        for paper in papers:
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
