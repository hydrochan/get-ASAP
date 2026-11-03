"""검토 UI(관리자) 회귀 테스트 - docs/review-ui-design.md §1. 합성 데이터·합성 세션만 쓴다.

- store.list_review_papers: 필터(상태·월·q)·건수·정렬·페이징·tombstone/dup 제외·잘못된 인자
- store.set_human_status: human 출처 고정(status 불변이어도), seq/status_seq/status_history(actor), 피드 전달,
  충돌 규칙으로 오래된 자동 판정으로부터 보호, gone/invalid, 배치 원자성, 구 DB(actor 열 없음) 호환
- POST /api/admin/review/{papers,status}: 401/403/Origin 거부/no-store/본문 상한/해피패스 (실제 ThreadingHTTPServer)
"""
import datetime as dt
import http.client
import http.server
import json
import threading
from types import SimpleNamespace

import pytest

import store
from models import PaperMetadata

TODAY = dt.date(2026, 9, 20)
ADMIN = "pytest-admin"
SYNC_TOKEN = "synthetic-review-sync-token-0123456789"


def paper(title, journal="Synthetic Journal", date="2026-09-07", url="https://example.invalid/p"):
    return PaperMetadata(title=title, journal=journal, date=date, url=url)


@pytest.fixture(autouse=True)
def fixed_time(monkeypatch):
    """오늘=2026-09-20, _now()는 호출마다 1초씩 증가."""
    tick = {"n": 0}

    def now():
        tick["n"] += 1
        return (dt.datetime(2026, 9, 20, tzinfo=dt.timezone.utc) + dt.timedelta(seconds=tick["n"])).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

    monkeypatch.setattr(store, "_now", now)
    monkeypatch.setattr(store, "_today", lambda: TODAY)


def row(conn, title):
    return conn.execute("SELECT * FROM papers WHERE title=?", (title,)).fetchone()


def add(conn, title, *, status="불확실", journal="Synthetic Journal", date="2026-09-07",
        created="2026-09-10T00:00:00Z", source="get_asap", reason=""):
    """save_papers로 넣은 뒤 목록 검증에 필요한 열만 SQL로 직접 세팅하고 ref를 돌려준다(seq 규칙은 이 테스트의 관심사 아님)."""
    store.save_papers([paper(title, journal, date)], conn)
    conn.execute(
        "UPDATE papers SET status=?, status_source=?, created_at=?, gpt_reason=? WHERE title=?",
        (status, source, created, reason, title),
    )
    return row(conn, title)["ref"]


def titles(result):
    return [r["title"] for r in result["rows"]]


# ======================= store.list_review_papers =======================

def test_list_defaults_to_uncertain_and_row_shape(conn):
    ref = add(conn, "Uncertain one", reason="why")
    add(conn, "Relevant one", status="관련")
    out = store.list_review_papers(conn)
    assert titles(out) == ["Uncertain one"] and out["total"] == 1
    assert out["rows"][0] == {
        "ref": ref, "title": "Uncertain one", "journal": "Synthetic Journal", "date": "2026-09-07",
        "url": "https://example.invalid/p", "status": "불확실", "status_source": "get_asap", "gpt_reason": "why",
        "created_at": "2026-09-10T00:00:00Z", "bucket_month": "2026-09",
        "ai_article_kind": None, "article_kind_source": None,
    }


def test_list_status_filter_all_and_counts_include_every_label(conn):
    for i, st in enumerate(store.STATUSES):
        add(conn, f"T-{st}", status=st, created=f"2026-09-{10 + i:02d}T00:00:00Z")
    add(conn, "T-extra", status="불확실")
    allrows = store.list_review_papers(conn, "all")
    assert allrows["total"] == 7 and len(allrows["rows"]) == 7
    assert allrows["counts"] == {"대기중": 1, "관련": 1, "불확실": 2, "다운완료": 1, "무관": 1, "실패": 1}
    one = store.list_review_papers(conn, "불확실")
    assert one["total"] == 2 and one["counts"] == allrows["counts"]  # counts는 선택한 status와 무관
    assert store.list_review_papers(conn, "무관")["total"] == 1
    empty = store.list_review_papers(conn, "실패", month="2026-05")
    assert empty["rows"] == [] and empty["total"] == 0 and set(empty["counts"]) == set(store.STATUSES)
    assert all(v == 0 for v in empty["counts"].values())


def test_list_month_filter_applies_to_rows_and_counts_but_months_lists_all(conn):
    add(conn, "Sep A", status="불확실", date="2026-09-01")
    add(conn, "Sep B", status="관련", date="2026-09-02")
    add(conn, "Aug A", status="불확실", date="2026-08-05")
    add(conn, "May A", status="무관", date="2026-05-05")
    out = store.list_review_papers(conn, "all", month="2026-09")
    assert sorted(titles(out)) == ["Sep A", "Sep B"]
    assert out["counts"]["불확실"] == 1 and out["counts"]["관련"] == 1 and out["counts"]["무관"] == 0
    assert out["months"] == ["2026-09", "2026-08", "2026-05"]  # month 조건과 무관한 전체 목록, 내림차순
    assert titles(store.list_review_papers(conn, "불확실", month="2026-08")) == ["Aug A"]
    assert store.list_review_papers(conn, "all", month="")["total"] == 4  # 빈 문자열 = 월 조건 없음


def test_list_q_is_case_insensitive_normalized_substring_on_title_and_journal(conn):
    add(conn, "Deep   Learning for Catalysts", journal="Nature Catalysis", status="관련")
    add(conn, "MÜLLER effect in electrodes", journal="Joule", status="불확실")
    add(conn, "100% yield claims", journal="ACS Energy Letters", status="불확실")
    add(conn, "Ni–Co alloys", journal="Joule", status="관련")  # en dash
    add(conn, "Unrelated title", journal="Joule", status="무관")

    def q(text, status="all", **kw):
        return store.list_review_papers(conn, status, q=text, **kw)

    assert titles(q("DEEP learning")) == ["Deep   Learning for Catalysts"]  # 대소문자·공백 정규화
    assert titles(q("nature catalysis")) == ["Deep   Learning for Catalysts"]  # 저널 일치
    assert titles(q("müller")) == ["MÜLLER effect in electrodes"]  # 비ASCII 대소문자 접기(LIKE로는 안 됨)
    assert titles(q("MÜLLER")) == ["MÜLLER effect in electrodes"]
    assert titles(q("ni-co")) == ["Ni–Co alloys"]  # 대시 통일
    assert titles(q("100%")) == ["100% yield claims"]
    assert titles(q("%")) == ["100% yield claims"]  # 와일드카드가 아니라 리터럴
    assert titles(q("_")) == []
    assert sorted(titles(q("joule"))) == ["MÜLLER effect in electrodes", "Ni–Co alloys", "Unrelated title"]
    assert store.list_review_papers(conn, "all", q="   ")["total"] == 5  # 공백뿐이면 조건 없음
    assert store.list_review_papers(conn, "all", q=None)["total"] == 5
    # counts는 같은 q 조건에서 상태별 건수다
    out = q("joule", "불확실")
    assert out["counts"]["불확실"] == 1 and out["counts"]["관련"] == 1 and out["counts"]["무관"] == 1
    assert out["counts"]["대기중"] == 0 and out["total"] == 1
    # month와 q 결합
    assert titles(q("joule", "all", month="2026-08")) == []


def test_list_excludes_tombstones_and_duplicate_rows_from_rows_counts_and_months(conn):
    add(conn, "Kept", status="불확실")
    add(conn, "Dead", status="불확실", date="2026-07-01")
    conn.execute("UPDATE papers SET deleted_at='2026-09-15T00:00:00Z' WHERE title='Dead'")
    with store.write_tx(conn):  # 같은 title_key로 이관 중복 행 삽입 -> dup_of 설정
        store.upsert_migrated(conn, {
            "ref": "00000000-0000-4000-8000-000000000001", "title": "kept", "bucket_month": "2026-08",
            "created_at": "2026-08-01T00:00:00Z", "status": "불확실",
        })
    assert conn.execute("SELECT COUNT(*) FROM papers WHERE dup_of IS NOT NULL").fetchone()[0] == 1
    out = store.list_review_papers(conn, "all")
    assert titles(out) == ["Kept"] and out["total"] == 1 and out["counts"]["불확실"] == 1
    assert out["months"] == ["2026-09"]  # tombstone의 2026-07, dup의 2026-08 bucket은 나오지 않는다


def test_list_orders_by_created_at_desc_then_id_desc_and_pages_without_gaps_or_overlap(conn):
    for i in range(25):
        # 5개씩 같은 created_at(동률) -> id DESC로 안정 정렬
        add(conn, f"Paper {i:02d}", created=f"2026-09-{10 + i // 5:02d}T00:00:00Z")
    full = titles(store.list_review_papers(conn, "불확실", limit=500))
    assert len(full) == 25
    # created_at은 5개씩 동률이고 id는 입력 순서대로 커지므로, created_at DESC + id DESC = 입력의 역순
    assert full == [f"Paper {i:02d}" for i in reversed(range(25))]

    seen = []
    for offset in range(0, 30, 10):
        page = store.list_review_papers(conn, "불확실", limit=10, offset=offset)
        assert page["total"] == 25
        seen += titles(page)
    assert seen == full  # 누락·중복 없이 이어 붙는다
    assert store.list_review_papers(conn, "불확실", limit=10, offset=25)["rows"] == []


@pytest.mark.parametrize("kw", [
    {"status": "bogus"}, {"status": ""}, {"month": "2026-13"}, {"month": "26-09"}, {"limit": 0}, {"limit": 501},
    {"limit": True}, {"limit": "5"}, {"offset": -1}, {"offset": 1.5}, {"q": 5},
])
def test_list_rejects_bad_arguments(conn, kw):
    with pytest.raises(ValueError):
        store.list_review_papers(conn, **kw)


# ======================= 글 종류(AI 비논문·논평 표시) 필터 / 건수 =======================

def set_kind(conn, title, kind, source="ai"):
    conn.execute("UPDATE papers SET ai_article_kind=?, article_kind_source=? WHERE title=?", (kind, source, title))


def seed_kinds(conn):
    add(conn, "K research", status="관련")
    add(conn, "K non A", status="불확실")
    add(conn, "K non B", status="무관", date="2026-08-05")
    add(conn, "K comment", status="관련")
    add(conn, "K review", status="불확실")
    add(conn, "K unmarked", status="불확실")
    set_kind(conn, "K research", "research")
    set_kind(conn, "K non A", "non_research")
    set_kind(conn, "K non B", "non_research")
    set_kind(conn, "K comment", "comment")
    set_kind(conn, "K review", "review")


def test_review_list_shows_hidden_rows_and_filters_by_kind(conn):
    seed_kinds(conn)
    allrows = store.list_review_papers(conn, "all")
    assert allrows["total"] == 6  # 관리자 목록은 숨김 필터를 적용하지 않는다
    by_title = {r["title"]: r for r in allrows["rows"]}
    assert (by_title["K non A"]["ai_article_kind"], by_title["K non A"]["article_kind_source"]) == ("non_research", "ai")
    assert by_title["K unmarked"]["ai_article_kind"] is None

    def kinds(kind, status="all", **kw):
        return sorted(titles(store.list_review_papers(conn, status, kind=kind, **kw)))

    assert kinds("non_research") == ["K non A", "K non B"]
    assert kinds("comment") == ["K comment"]
    assert kinds("review") == ["K review"]
    assert kinds("hidden") == ["K comment", "K non A", "K non B"]
    assert kinds("hidden", "불확실") == ["K non A"]                      # status 와 겹쳐 좁힌다
    assert kinds("hidden", "all", month="2026-08") == ["K non B"]        # month 와도
    assert kinds("hidden", "all", q="non a") == ["K non A"]              # q 와도
    assert kinds(None) == sorted(titles(allrows))


def test_review_counts_follow_kind_filter_but_kind_counts_do_not(conn):
    seed_kinds(conn)
    base = store.list_review_papers(conn, "all")
    assert base["kind_counts"] == {"non_research": 2, "comment": 1, "review": 1, "hidden": 3}
    hidden = store.list_review_papers(conn, "불확실", kind="hidden")
    assert hidden["counts"] == {"대기중": 0, "관련": 1, "불확실": 1, "다운완료": 0, "무관": 1, "실패": 0}  # 숨김 행 기준
    assert hidden["total"] == 1
    assert hidden["kind_counts"] == base["kind_counts"]                 # 칩 건수는 kind·status 필터와 무관
    # month·q 조건에는 따라간다
    aug = store.list_review_papers(conn, "all", month="2026-08")
    assert aug["kind_counts"] == {"non_research": 1, "comment": 0, "review": 0, "hidden": 1}
    assert store.list_review_papers(conn, "all", q="K review")["kind_counts"]["review"] == 1
    # tombstone·dup 행은 세지 않는다
    conn.execute("UPDATE papers SET deleted_at='2026-09-15T00:00:00Z' WHERE title='K non A'")
    assert store.list_review_papers(conn, "all")["kind_counts"]["hidden"] == 2


@pytest.mark.parametrize("kind", ["bogus", "", "research", "flagged", 5, ["hidden"]])
def test_review_list_rejects_bad_kind(conn, kind):
    with pytest.raises(ValueError):
        store.list_review_papers(conn, kind=kind)


# ======================= store.set_human_article_kind (복원 / 직접 숨김) =======================

def test_restore_hidden_row_marks_human_dirties_csv_and_leaves_feed_and_seq_alone(conn, tmp_path):
    ref = add(conn, "Wrongly hidden", status="관련", source="paper_autodown")
    store.apply_verdicts(conn, [{"ref": ref, "article_kind": "non_research"}], 0)
    store.export_all(conn, str(tmp_path / "cache"))
    assert "csv_seq:2026-09" in dict(conn.execute("SELECT key, value FROM meta").fetchall())
    before = dict(row(conn, "Wrongly hidden"))
    seq0, cursor = store.last_seq(conn), store.last_seq(conn)

    out = store.set_human_article_kind(conn, [{"ref": ref, "kind": "research"}], ADMIN)["results"][0]
    assert out["result"] == "updated" and out["ref"] == ref
    assert (out["current"]["ai_article_kind"], out["current"]["article_kind_source"]) == ("research", "human")
    assert set(out["current"]) == set(store.list_review_papers(conn, "all")["rows"][0])  # 목록 행과 같은 모양
    after = dict(row(conn, "Wrongly hidden"))
    assert {k: v for k, v in after.items() if k not in ("ai_article_kind", "article_kind_source")} == \
        {k: v for k, v in before.items() if k not in ("ai_article_kind", "article_kind_source")}  # status·change_seq·updated_at 불변
    assert store.last_seq(conn) == seq0 and store.fetch_changes(conn, cursor)["rows"] == []
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 0
    assert "csv_seq:2026-09" not in dict(conn.execute("SELECT key, value FROM meta").fetchall())  # 다음 export_dirty 가 다시 쓴다
    assert store.export_dirty(conn, str(tmp_path / "cache")) == {"2026-09": 1}

    # 같은 지정을 다시 하면 unchanged (아무것도 쓰지 않는다)
    again = store.set_human_article_kind(conn, [{"ref": ref, "kind": "research"}], ADMIN)["results"][0]
    assert again["result"] == "unchanged" and dict(row(conn, "Wrongly hidden")) == after
    assert store.export_dirty(conn, str(tmp_path / "cache")) == {}


def test_human_kind_wins_over_later_ai_marks_and_admin_can_hide(conn):
    ref = add(conn, "Admin hides", status="관련")
    res = store.set_human_article_kind(conn, [{"ref": ref, "kind": "non_research"}], ADMIN)["results"][0]
    assert res["result"] == "updated" and res["current"]["article_kind_source"] == "human"
    assert store.apply_verdicts(conn, [{"ref": ref, "article_kind": "research"}], 0)["results"][0]["result"] == "unchanged"
    assert (row(conn, "Admin hides")["ai_article_kind"], row(conn, "Admin hides")["article_kind_source"]) == ("non_research", "human")
    store.set_human_article_kind(conn, [{"ref": ref, "kind": "research"}], ADMIN)
    assert store.apply_verdicts(conn, [{"ref": ref, "article_kind": "non_research"}], 0)["results"][0]["result"] == "unchanged"
    assert row(conn, "Admin hides")["ai_article_kind"] == "research"


def test_human_kind_already_ai_research_becomes_human_without_csv_dirty(conn, tmp_path):
    ref = add(conn, "Visible", status="관련")
    store.apply_verdicts(conn, [{"ref": ref, "article_kind": "research"}], 0)
    store.export_all(conn, str(tmp_path / "cache"))
    res = store.set_human_article_kind(conn, [{"ref": ref, "kind": "research"}], ADMIN)["results"][0]
    assert res["result"] == "updated" and res["current"]["article_kind_source"] == "human"  # 출처만 바뀜
    assert store.export_dirty(conn, str(tmp_path / "cache")) == {}  # 숨김 여부는 그대로라 CSV 를 다시 쓰지 않는다


def test_human_kind_gone_invalid_and_bad_actor(conn):
    good = add(conn, "Good kind row")
    dead = add(conn, "Dead kind row", date="2026-08-01")
    conn.execute("UPDATE papers SET deleted_at='2026-09-15T00:00:00Z' WHERE ref=?", (dead,))
    items = [
        {"ref": "no-such"}, {"ref": "no-such", "kind": "research"}, {"ref": dead, "kind": "research"},
        {"ref": good, "kind": "comment"}, {"ref": good, "kind": "review"}, {"ref": good, "kind": None}, {"ref": good},
        {"ref": good, "kind": ["research"]}, {"ref": "", "kind": "research"}, {"ref": 7, "kind": "research"}, "x", None,
        {"ref": good, "kind": "non_research"},
    ]
    results = store.set_human_article_kind(conn, items, ADMIN)["results"]
    assert [r["result"] for r in results] == [
        "invalid", "gone", "gone", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid",
        "invalid", "updated",
    ]
    assert row(conn, "Good kind row")["ai_article_kind"] == "non_research" and row(conn, "Dead kind row")["ai_article_kind"] is None
    assert store.set_human_article_kind(conn, [], ADMIN) == {"results": []}
    for bad in ("", "  ", None, 5):
        with pytest.raises(ValueError):
            store.set_human_article_kind(conn, [], bad)


def test_legacy_db_without_kind_columns_is_upgraded_lazily(conn, tmp_path):
    ref = add(conn, "Legacy kind", status="관련")
    conn.execute("ALTER TABLE papers DROP COLUMN ai_article_kind")   # 이 기능 이전에 만든 운영 DB 를 흉내 낸다
    conn.execute("ALTER TABLE papers DROP COLUMN article_kind_source")
    # 공개 export(읽기 전용 슬림 지연 생성 포함)는 열이 없어도 동작하고 아무것도 숨기지 않는다
    assert store.export_all(conn, str(tmp_path / "cache")) == {"2026-09": 1}
    ro = store.connect(str(conn.execute("PRAGMA database_list").fetchone()[2]), readonly=True)
    try:
        assert len(store._csv_rows(ro, "2026-09")[1]) == 1
    finally:
        ro.close()
    # 검토 목록은 열을 추가하고 정상 응답한다
    out = store.list_review_papers(conn, "all")
    assert out["rows"][0]["ai_article_kind"] is None and out["kind_counts"]["hidden"] == 0
    assert "ai_article_kind" in [r[1] for r in conn.execute("PRAGMA table_info(papers)")]
    # apply_verdicts·사람 지정도 마찬가지
    conn.execute("ALTER TABLE papers DROP COLUMN ai_article_kind")
    conn.execute("ALTER TABLE papers DROP COLUMN article_kind_source")
    assert store.apply_verdicts(conn, [{"ref": ref, "article_kind": "comment"}], 0)["results"][0]["result"] == "updated"
    assert row(conn, "Legacy kind")["ai_article_kind"] == "comment"
    store.init_schema(conn)  # 멱등


# ======================= store.set_human_status =======================

def seq_row(conn, title):
    r = row(conn, title)
    return r["status"], r["status_source"], r["status_seq"], r["change_seq"]


def test_status_change_marks_human_bumps_seqs_and_logs_actor(conn):
    ref = add(conn, "Mover", status="불확실", source="paper_autodown")
    seq0 = store.last_seq(conn)
    out = store.set_human_status(conn, [{"ref": ref, "status": "관련"}], ADMIN)
    res = out["results"][0]
    assert res["result"] == "updated" and res["ref"] == ref
    assert res["current"]["status"] == "관련" and res["current"]["status_source"] == "human"
    assert set(res["current"]) == set(store.list_review_papers(conn, "all")["rows"][0])  # 목록 행과 같은 모양
    assert store.last_seq(conn) == seq0 + 1
    assert seq_row(conn, "Mover") == ("관련", "human", seq0 + 1, seq0 + 1)
    hist = conn.execute(
        "SELECT old_status,new_status,old_source,new_source,seq,actor FROM status_history ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert tuple(hist) == ("불확실", "관련", "paper_autodown", "human", seq0 + 1, ADMIN)


def test_same_status_still_becomes_human_and_is_tracked_by_the_feed(conn):
    """status 불변이어도 출처가 human으로 바뀌고, 그 변경이 피드(change_seq)로 전달된다."""
    ref = add(conn, "Confirmed by hand", status="불확실", source="paper_autodown")
    cursor = store.last_seq(conn)  # 클라이언트가 여기까지 받았다고 가정
    before = row(conn, "Confirmed by hand")
    out = store.set_human_status(conn, [{"ref": ref, "status": "불확실"}], ADMIN)["results"][0]
    assert out["result"] == "updated"  # 쓰기가 있었으므로 updated (unchanged는 이미 human일 때만)
    after = row(conn, "Confirmed by hand")
    assert (after["status"], after["status_source"]) == ("불확실", "human")
    assert after["change_seq"] == cursor + 1 == after["status_seq"] == store.last_seq(conn)
    assert after["updated_at"] > before["updated_at"]

    feed = store.fetch_changes(conn, cursor)
    assert [(r["ref"], r["status"], r["status_source"], r["status_seq"]) for r in feed["rows"]] == [
        (ref, "불확실", "human", cursor + 1)
    ]
    hist = conn.execute("SELECT old_status,new_status,old_source,new_source,actor FROM status_history").fetchall()
    assert [tuple(h) for h in hist] == [("불확실", "불확실", "paper_autodown", "human", ADMIN)]


def test_already_human_same_status_is_unchanged_and_writes_nothing(conn):
    ref = add(conn, "Settled", status="관련", source="get_asap")
    store.set_human_status(conn, [{"ref": ref, "status": "관련"}], ADMIN)
    before, seq0 = dict(row(conn, "Settled")), store.last_seq(conn)
    hist0 = conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0]
    res = store.set_human_status(conn, [{"ref": ref, "status": "관련"}], "someone-else")["results"][0]
    assert res["result"] == "unchanged" and res["current"]["status_source"] == "human"
    assert dict(row(conn, "Settled")) == before and store.last_seq(conn) == seq0
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == hist0


def test_human_override_of_human_status_and_all_four_labels(conn):
    ref = add(conn, "Cycler", status="대기중")
    for label in store.HUMAN_STATUSES:
        res = store.set_human_status(conn, [{"ref": ref, "status": label}], ADMIN)["results"][0]
        assert res["result"] in ("updated", "unchanged") and res["current"]["status"] == label
        assert row(conn, "Cycler")["status"] == label and row(conn, "Cycler")["status_source"] == "human"
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 4


def test_gone_and_invalid_results_do_not_abort_the_batch(conn):
    good = add(conn, "Good row", status="불확실")
    dead = add(conn, "Dead row", status="불확실", date="2026-08-01")
    conn.execute("UPDATE papers SET deleted_at='2026-09-15T00:00:00Z' WHERE ref=?", (dead,))
    seq0 = store.last_seq(conn)
    items = [
        {"ref": "no-such-ref", "status": "관련"},
        {"ref": dead, "status": "관련"},
        {"ref": good, "status": "대기중"},   # 시스템 상태는 사람이 지정할 수 없다
        {"ref": good, "status": "실패"},
        {"ref": good, "status": None},
        {"ref": good},
        {"ref": good, "status": ["관련"]},
        {"ref": "", "status": "관련"},
        {"ref": 7, "status": "관련"},
        "not-a-dict",
        None,
        {"ref": good, "status": "무관"},
    ]
    results = store.set_human_status(conn, items, ADMIN)["results"]
    assert [r["result"] for r in results] == [
        "gone", "gone", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid", "invalid",
        "invalid", "updated",
    ]
    assert results[0]["ref"] == "no-such-ref" and "current" not in results[0]
    assert results[8]["ref"] is None and results[9]["ref"] is None
    assert row(conn, "Good row")["status"] == "무관" and store.last_seq(conn) == seq0 + 1
    assert row(conn, "Dead row")["status"] == "불확실"  # tombstone은 건드리지 않는다


def test_empty_batch_and_bad_actor(conn):
    assert store.set_human_status(conn, [], ADMIN) == {"results": []}
    for bad in ("", "   ", None, 5):
        with pytest.raises(ValueError):
            store.set_human_status(conn, [], bad)


def test_batch_is_atomic(conn, monkeypatch):
    refs = [add(conn, f"Atomic {i}", status="불확실") for i in range(3)]
    seq0 = store.last_seq(conn)
    real = store._update_paper
    calls = {"n": 0}

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("boom")
        return real(*a, **kw)

    monkeypatch.setattr(store, "_update_paper", flaky)
    with pytest.raises(RuntimeError):
        store.set_human_status(conn, [{"ref": r, "status": "관련"} for r in refs], ADMIN)
    assert store.last_seq(conn) == seq0
    assert {r[0] for r in conn.execute("SELECT status FROM papers")} == {"불확실"}
    assert conn.execute("SELECT COUNT(*) FROM status_history").fetchone()[0] == 0
    assert not conn.in_transaction


def test_same_ref_twice_in_one_batch_last_wins(conn):
    ref = add(conn, "Twice", status="불확실")
    res = store.set_human_status(conn, [{"ref": ref, "status": "관련"}, {"ref": ref, "status": "무관"}], ADMIN)["results"]
    assert [r["result"] for r in res] == ["updated", "updated"] and row(conn, "Twice")["status"] == "무관"


# ---- 충돌 규칙: 사람 판정은 오래된 자동 판정으로부터 보호된다 ----

def test_human_status_change_is_protected_from_stale_autodown_verdict(conn):
    ref = add(conn, "Protected", status="불확실", source="paper_autodown")
    seen_through = store.last_seq(conn)  # paper_autodown이 마지막으로 본 seq
    store.set_human_status(conn, [{"ref": ref, "status": "관련"}], ADMIN)
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "무관", "gpt_reason": "bot"}], seen_through)["results"][0]
    assert res["result"] == "conflict" and res["current"]["status"] == "관련" and res["current"]["status_source"] == "human"
    assert row(conn, "Protected")["gpt_reason"] == ""
    # 사람 판정을 본 뒤(seen_through >= status_seq)라면 덮어쓸 수 있고 출처는 paper_autodown으로 넘어간다
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "무관"}], store.last_seq(conn))["results"][0]
    assert res["result"] == "updated" and row(conn, "Protected")["status_source"] == "paper_autodown"


def test_source_only_human_confirmation_is_protected_too(conn):
    """status가 같아 출처만 human으로 바뀐 경우에도 status_seq가 새로워져 충돌 규칙이 지켜 준다."""
    ref = add(conn, "Confirmed", status="불확실", source="paper_autodown")
    seen_through = store.last_seq(conn)
    store.set_human_status(conn, [{"ref": ref, "status": "불확실"}], ADMIN)
    assert row(conn, "Confirmed")["status_seq"] > seen_through
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "관련"}], seen_through)["results"][0]
    assert res["result"] == "conflict"
    assert seq_row(conn, "Confirmed")[:2] == ("불확실", "human")
    # 같은 status를 다시 밀어 넣는 것은 충돌이 아니다(사유만 갱신, 출처는 human 유지)
    res = store.apply_verdicts(conn, [{"ref": ref, "status": "불확실", "gpt_reason": "agree"}], seen_through)["results"][0]
    assert res["result"] == "updated" and row(conn, "Confirmed")["status_source"] == "human"


# ---- 구 DB 호환 ----

def test_schema_has_actor_and_legacy_db_without_it_is_upgraded_lazily(conn):
    assert "actor" in [r[1] for r in conn.execute("PRAGMA table_info(status_history)")]
    ref = add(conn, "Legacy", status="불확실", source="paper_autodown")
    conn.execute("ALTER TABLE status_history DROP COLUMN actor")  # 검토 UI 이전에 만든 DB를 흉내 낸다
    # 기존 경로(apply_verdicts)는 actor 열 없이도 그대로 동작한다
    other = add(conn, "Legacy 2", status="불확실")
    assert store.apply_verdicts(conn, [{"ref": other, "status": "관련"}], 0)["results"][0]["result"] == "updated"
    assert "actor" not in [r[1] for r in conn.execute("PRAGMA table_info(status_history)")]
    # 사람 판정은 열을 추가하고 기록한다
    store.set_human_status(conn, [{"ref": ref, "status": "관련"}], ADMIN)
    assert "actor" in [r[1] for r in conn.execute("PRAGMA table_info(status_history)")]
    assert conn.execute("SELECT actor FROM status_history WHERE paper_id=(SELECT id FROM papers WHERE ref=?)", (ref,)).fetchone()[0] == ADMIN
    store.init_schema(conn)  # 멱등 - 다시 호출해도 오류 없음


def test_autodown_status_history_has_null_actor(conn):
    ref = add(conn, "Bot row", status="대기중")
    store.apply_verdicts(conn, [{"ref": ref, "status": "관련"}], 0)
    assert conn.execute("SELECT actor FROM status_history").fetchone()[0] is None


# ======================= HTTP 엔드포인트 =======================

@pytest.fixture
def web(db_file, monkeypatch):
    import config
    import server

    monkeypatch.setattr(config, "DASHBOARD_ADMINS", {ADMIN})
    monkeypatch.setenv("ASAP_SYNC_TOKEN", SYNC_TOKEN)
    server.sessions.clear()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), server.DashboardHandler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    admin_cookie = server._create_session(ADMIN)
    user_cookie = server._create_session("pytest-plain-user")
    origin = f"http://127.0.0.1:{port}"

    def call(path, body=None, *, cookie=admin_cookie, origin_header=origin, referer=None, raw=None, method="POST",
             extra=None):
        c = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        headers = dict(extra or {})
        if cookie:
            headers["Cookie"] = f"session={cookie}"
        if origin_header is not None:
            headers["Origin"] = origin_header
        if referer is not None:
            headers["Referer"] = referer
        payload = raw if raw is not None else (json.dumps(body if body is not None else {}).encode() if method == "POST" else None)
        if payload is not None:
            headers["Content-Type"] = "application/json"
        c.request(method, path, body=payload, headers=headers)
        resp = c.getresponse()
        data = resp.read()
        out = SimpleNamespace(status=resp.status, headers={k.lower(): v for k, v in resp.getheaders()}, data=data)
        out.json = json.loads(data) if data and out.headers.get("content-type", "").startswith("application/json") else None
        c.close()
        return out

    yield SimpleNamespace(call=call, origin=origin, port=port, admin=admin_cookie, user=user_cookie, server=server,
                          db_file=db_file)
    httpd.shutdown()
    httpd.server_close()
    server.sessions.clear()


PAPERS = "/api/admin/review/papers"
STATUS = "/api/admin/review/status"
KIND = "/api/admin/review/kind"
ENDPOINTS = [PAPERS, STATUS, KIND]
NO_STORE = "private, no-store"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_no_session_is_401_and_bad_session_is_401(web, path):
    for cookie in (None, "", "deadbeef" * 8):
        r = web.call(path, {"items": []}, cookie=cookie)
        assert r.status == 401 and r.json == {"error": "unauthorized"}
        assert r.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize("path", ENDPOINTS)
def test_non_admin_is_403(web, path):
    r = web.call(path, {"items": []}, cookie=web.user)
    assert r.status == 403 and r.json == {"error": "forbidden"} and r.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize("path", ENDPOINTS)
def test_expired_session_is_401(web, path):
    web.server.sessions[web.admin]["expires"] = 0
    assert web.call(path, {"items": []}).status == 401


@pytest.mark.parametrize("path", ENDPOINTS)
def test_auth_is_checked_before_origin(web, path):
    assert web.call(path, {}, cookie=None, origin_header="https://evil.example").status == 401
    assert web.call(path, {}, cookie=web.user, origin_header="https://evil.example").status == 403
    r = web.call(path, {}, origin_header="https://evil.example")
    assert r.status == 403 and r.json == {"error": "bad_origin"}  # 관리자인데 origin이 틀림


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("kw", [
    dict(origin_header="https://evil.example"),
    dict(origin_header="null"),
    dict(origin_header=""),
    dict(origin_header=None),                                                 # Origin·Referer 모두 없음
    dict(origin_header="http://127.0.0.1:1"),                                 # 포트가 다르면 다른 origin
    dict(origin_header="http://127.0.0.1.evil.example"),
    dict(origin_header=None, referer="https://evil.example/page"),
    dict(origin_header=None, referer="not a url"),
    dict(origin_header=None, referer=""),
    dict(origin_header="https://evil.example", referer="SAME"),               # Origin이 있으면 Referer로 되돌아가지 않는다
    dict(origin_header="javascript://127.0.0.1"),
])
def test_cross_origin_or_missing_origin_is_403_with_no_store(web, path, kw):
    kw = dict(kw)
    if kw.get("referer") == "SAME":
        kw["referer"] = web.origin + "/"
    r = web.call(path, {"items": []}, **kw)
    assert r.status == 403 and r.json == {"error": "bad_origin"}
    assert r.headers["cache-control"] == NO_STORE


def test_rejected_origin_never_writes(web, conn):
    add(conn, "Do not touch", status="불확실")
    ref = row(conn, "Do not touch")["ref"]
    seq0 = store.last_seq(conn)
    r = web.call(STATUS, {"items": [{"ref": ref, "status": "관련"}]}, origin_header="https://evil.example")
    assert r.status == 403
    assert store.last_seq(conn) == seq0 and row(conn, "Do not touch")["status"] == "불확실"


@pytest.mark.parametrize("path", ENDPOINTS)
def test_same_origin_via_origin_or_referer_is_accepted(web, path):
    assert web.call(path, {"items": []}).status == 200
    assert web.call(path, {"items": []}, origin_header=None, referer=web.origin + "/some/page?x=1").status == 200


def test_papers_happy_path_shape_default_status_and_no_store(web, conn):
    add(conn, "Alpha paper", status="불확실", journal="Joule", created="2026-09-12T00:00:00Z", reason="because")
    add(conn, "Beta paper", status="관련", created="2026-09-11T00:00:00Z")
    add(conn, "Gamma paper", status="불확실", date="2026-08-03", created="2026-09-13T00:00:00Z")
    r = web.call(PAPERS, {})
    assert r.status == 200 and r.headers["cache-control"] == NO_STORE
    assert r.headers["content-type"].startswith("application/json")
    assert [x["title"] for x in r.json["rows"]] == ["Gamma paper", "Alpha paper"]  # 기본 status=불확실, created_at DESC
    assert r.json["total"] == 2
    assert r.json["counts"] == {"대기중": 0, "관련": 1, "불확실": 2, "다운완료": 0, "무관": 0, "실패": 0}
    assert set(r.json["rows"][0]) == {"ref", "title", "journal", "date", "url", "status", "status_source", "gpt_reason",
                                      "created_at", "bucket_month", "ai_article_kind", "article_kind_source"}
    assert r.json["kind_counts"] == {"non_research": 0, "comment": 0, "review": 0, "hidden": 0}
    assert r.json["months"] == ["2026-09", "2026-08"]
    # 필터·페이징
    assert [x["title"] for x in web.call(PAPERS, {"status": "all", "limit": 2}).json["rows"]] == ["Gamma paper", "Alpha paper"]
    page2 = web.call(PAPERS, {"status": "all", "limit": 2, "offset": 2}).json
    assert [x["title"] for x in page2["rows"]] == ["Beta paper"] and page2["total"] == 3
    assert [x["title"] for x in web.call(PAPERS, {"month": "2026-08"}).json["rows"]] == ["Gamma paper"]
    assert [x["title"] for x in web.call(PAPERS, {"status": "all", "q": "JOULE"}).json["rows"]] == ["Alpha paper"]
    # 빈 값은 기본값으로 취급
    assert web.call(PAPERS, {"status": None, "month": None, "q": None, "limit": None, "offset": None}).json["total"] == 2
    assert web.call(PAPERS, raw=b"").status == 200  # 빈 본문 = {}


def test_papers_kind_filter_counts_and_hidden_rows_over_http(web, conn):
    seed_kinds(conn)
    r = web.call(PAPERS, {"status": "all", "kind": "hidden"})
    assert r.status == 200 and r.headers["cache-control"] == NO_STORE
    assert sorted(x["title"] for x in r.json["rows"]) == ["K comment", "K non A", "K non B"]
    assert r.json["kind_counts"] == {"non_research": 2, "comment": 1, "review": 1, "hidden": 3}
    assert {x["title"]: x["ai_article_kind"] for x in r.json["rows"]}["K comment"] == "comment"
    assert [x["title"] for x in web.call(PAPERS, {"status": "all", "kind": "review"}).json["rows"]] == ["K review"]
    assert web.call(PAPERS, {"status": "all", "kind": None}).json["total"] == 6
    assert web.call(PAPERS, {"status": "all", "kind": ""}).json["total"] == 6  # 빈 값 = 조건 없음


@pytest.mark.parametrize("body", [{"kind": "bogus"}, {"kind": 5}, {"kind": ["hidden"]}, {"kind": "research"}])
def test_papers_bad_kind_is_400(web, body):
    r = web.call(PAPERS, body)
    assert r.status == 400 and r.json == {"error": "bad_request"} and r.headers["cache-control"] == NO_STORE


def test_kind_endpoint_restore_and_hide_happy_path_records_human_and_dirties_csv(web, conn, tmp_path):
    ref = add(conn, "Restore me", status="관련", source="paper_autodown")
    ref2 = add(conn, "Hide me", status="관련", source="paper_autodown")
    store.apply_verdicts(conn, [{"ref": ref, "article_kind": "comment"}], 0)
    cache = str(tmp_path / "cache")
    store.export_all(conn, cache)
    seq0 = store.last_seq(conn)
    body = {"items": [{"ref": ref, "kind": "research"}, {"ref": ref2, "kind": "non_research"}, {"ref": "nope", "kind": "research"},
                      {"ref": ref, "kind": "comment"}]}
    r = web.call(KIND, body)
    assert r.status == 200 and r.headers["cache-control"] == NO_STORE
    assert [x["result"] for x in r.json["results"]] == ["updated", "updated", "gone", "invalid"]
    assert r.json["results"][0]["current"]["ai_article_kind"] == "research"
    assert r.json["results"][0]["current"]["article_kind_source"] == "human"
    assert r.json["results"][1]["current"]["ai_article_kind"] == "non_research"
    assert store.last_seq(conn) == seq0  # 피드를 타지 않는다
    assert store.export_dirty(conn, cache) == {"2026-09": 1}  # 복원 1건 + 숨김 1건 -> 보이는 행은 Restore me 하나
    again = web.call(KIND, {"items": body["items"][:2]})
    assert [x["result"] for x in again.json["results"]] == ["unchanged", "unchanged"]


@pytest.mark.parametrize("body", [{}, {"items": "x"}, {"items": {"ref": "a"}}, {"items": None}, {"items": [{}] * 201}])
def test_kind_bad_items_is_400(web, body):
    r = web.call(KIND, body)
    assert r.status == 400 and r.json == {"error": "bad_request"}


def test_rejected_origin_never_writes_kind(web, conn):
    ref = add(conn, "Kind untouched", status="관련")
    r = web.call(KIND, {"items": [{"ref": ref, "kind": "non_research"}]}, origin_header="https://evil.example")
    assert r.status == 403
    r = web.call(KIND, {"items": [{"ref": ref, "kind": "non_research"}]}, cookie=web.user)
    assert r.status == 403
    assert row(conn, "Kind untouched")["ai_article_kind"] is None


@pytest.mark.parametrize("body", [
    {"status": "bogus"}, {"status": 5}, {"status": ["불확실"]}, {"month": "2026-13"}, {"month": 202609}, {"q": 5},
    {"q": ["a"]}, {"limit": 0}, {"limit": 501}, {"limit": True}, {"limit": "10"}, {"limit": 1.5}, {"offset": -1},
    {"offset": "0"},
])
def test_papers_bad_arguments_are_400(web, body):
    r = web.call(PAPERS, body)
    assert r.status == 400 and r.json == {"error": "bad_request"} and r.headers["cache-control"] == NO_STORE


@pytest.mark.parametrize("path", ENDPOINTS)
@pytest.mark.parametrize("raw", [b"{not json", b"[1, 2]", b'"str"', b"123", b"null", b"\xff\xfe"])
def test_malformed_or_non_object_body_is_400(web, path, raw):
    r = web.call(path, raw=raw)
    assert r.status == 400 and r.json == {"error": "bad_request"}


def test_status_happy_path_updates_db_history_actor_and_second_call_is_unchanged(web, conn):
    ref = add(conn, "Review me", status="불확실", source="paper_autodown")
    ref2 = add(conn, "Review me too", status="관련", source="paper_autodown")
    body = {"items": [{"ref": ref, "status": "관련"}, {"ref": ref2, "status": "관련"}, {"ref": "nope", "status": "무관"},
                      {"ref": ref, "status": "대기중"}]}
    r = web.call(STATUS, body)
    assert r.status == 200 and r.headers["cache-control"] == NO_STORE
    assert [x["result"] for x in r.json["results"]] == ["updated", "updated", "gone", "invalid"]
    assert r.json["results"][0]["current"]["status"] == "관련" and r.json["results"][0]["current"]["status_source"] == "human"
    assert r.json["results"][1]["current"]["status_source"] == "human"  # status 불변이어도 human
    assert (row(conn, "Review me")["status"], row(conn, "Review me")["status_source"]) == ("관련", "human")
    assert {h[0] for h in conn.execute("SELECT actor FROM status_history")} == {ADMIN}  # actor = 세션의 관리자 ID
    seq0 = store.last_seq(conn)
    again = web.call(STATUS, {"items": body["items"][:2]})
    assert [x["result"] for x in again.json["results"]] == ["unchanged", "unchanged"]
    assert store.last_seq(conn) == seq0


@pytest.mark.parametrize("body", [{}, {"items": "x"}, {"items": {"ref": "a"}}, {"items": None}, {"items": [{}] * 201}])
def test_status_bad_items_is_400(web, body):
    r = web.call(STATUS, body)
    assert r.status == 400 and r.json == {"error": "bad_request"}


def test_status_accepts_exactly_200_items(web, conn):
    r = web.call(STATUS, {"items": [{"ref": f"missing-{i}", "status": "관련"} for i in range(200)]})
    assert r.status == 200 and len(r.json["results"]) == 200 and {x["result"] for x in r.json["results"]} == {"gone"}


@pytest.mark.parametrize("path", ENDPOINTS)
def test_oversized_body_is_413_before_reading_and_has_no_store(web, path):
    # Content-Length만 상한 초과로 선언하고 본문은 보내지 않는다: 서버가 읽으려 들면 타임아웃 난다
    c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=5)
    c.putrequest("POST", path)
    c.putheader("Cookie", f"session={web.admin}")
    c.putheader("Origin", web.origin)
    c.putheader("Content-Length", str(65536 + 1))
    c.endheaders()
    resp = c.getresponse()
    assert resp.status == 413 and json.loads(resp.read()) == {"error": "payload_too_large"}
    assert resp.getheader("Cache-Control") == NO_STORE
    c.close()


def test_body_at_the_cap_is_accepted(web):
    pad = b" " * (65536 - len(b"{}"))
    assert web.call(PAPERS, raw=b"{}" + pad).status == 200


def test_bad_content_length_is_400(web):
    c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=5)
    c.putrequest("POST", PAPERS)
    c.putheader("Content-Length", "abc")
    c.endheaders()
    assert c.getresponse().status == 400
    c.close()


@pytest.mark.parametrize("path", ENDPOINTS)
def test_get_is_not_served(web, path):
    r = web.call(path, method="GET")
    assert r.status == 404


@pytest.mark.parametrize("path", ENDPOINTS)
def test_missing_db_is_503_and_not_created(web, path, monkeypatch, tmp_path):
    ghost = tmp_path / "ghost" / "asap.db"
    monkeypatch.setenv("ASAP_DB_PATH", str(ghost))
    r = web.call(path, {"items": []})
    assert r.status == 503 and r.json == {"error": "db_unavailable"} and r.headers["cache-control"] == NO_STORE
    assert not ghost.exists() and not ghost.parent.exists()


def test_end_to_end_human_decision_blocks_stale_autodown_verdict_over_the_sync_api(web, conn):
    """검토 UI로 정한 판정이 변경 피드로 나가고, 그 판정을 보지 못한 paper_autodown의 verdict는 충돌로 거부된다."""
    ref = add(conn, "E2E row", status="불확실", source="paper_autodown")
    cursor = store.last_seq(conn)
    r = web.call(STATUS, {"items": [{"ref": ref, "status": "불확실"}]})  # 같은 status, 출처만 human
    assert r.json["results"][0]["result"] == "updated"

    def sync(endpoint, body):
        c = http.client.HTTPConnection("127.0.0.1", web.port, timeout=10)
        c.request("POST", "/api/sync/v1/" + endpoint, body=json.dumps(body).encode(),
                  headers={"Authorization": f"Bearer {SYNC_TOKEN}", "Content-Type": "application/json"})
        resp = c.getresponse()
        out = json.loads(resp.read())
        c.close()
        return out

    feed = sync("changes", {"after": cursor})
    assert [(x["ref"], x["status"], x["status_source"]) for x in feed["rows"]] == [(ref, "불확실", "human")]
    verdict = sync("verdicts", {"seen_through": cursor, "items": [{"ref": ref, "status": "무관", "gpt_reason": "bot"}]})
    assert verdict["results"][0]["result"] == "conflict"
    assert verdict["results"][0]["current"]["status_source"] == "human"
    assert row(conn, "E2E row")["status"] == "불확실"
    # 피드를 본 뒤(seen_through >= status_seq)에는 적용된다
    verdict = sync("verdicts", {"seen_through": feed["next_after"], "items": [{"ref": ref, "status": "무관"}]})
    assert verdict["results"][0]["result"] == "updated"


def test_public_csv_still_hides_status_after_human_review(web, conn, tmp_path, monkeypatch):
    """판정이 바뀌어도 공개 /api/csv 는 status 열을 뺀다(기존 계약 불변)."""
    ref = add(conn, "Public csv row", status="불확실", source="paper_autodown")
    web.call(STATUS, {"items": [{"ref": ref, "status": "관련"}]})
    monkeypatch.setattr(web.server, "CACHE_DIR", str(tmp_path / "cache"))
    store.export_all(conn, str(tmp_path / "cache"))
    r = web.call("/api/csv/2026-09", method="GET", cookie=None, origin_header=None)
    assert r.status == 200
    header = r.data.decode("utf-8-sig").splitlines()[0]
    assert "status" not in header and "title" in header
