#!/usr/bin/env python
"""title_fold·DOI 기준으로 놓친 중복 정본 행을 한 번 정리하는 일회성 운영 스크립트 (fix_title_subscripts.py 와 같은 성격).

배경:
  저장 시 중복 판정(store._insert_paper)이 title_key 정확 일치 + 공백만 다른 일치까지만 봐서, 끝 마침표·하이픈·기호·HTML 표기가
  다르거나 추적 URL 행과 DOI URL 행으로 따로 들어온 같은 논문이 정본 행(dup_of IS NULL, 살아 있음)으로 둘씩 남아 있다.
  이제 저장 경로는 store.title_fold / DOI 로 막는다. 이 스크립트는 이미 들어와 있는 것을 같은 규칙으로 정리한다.

대상·규칙 (store._DedupIndex 와 같은 판정):
  살아 있는(deleted_at IS NULL) 정본 행(dup_of IS NULL)을 (a) title_fold 가 같고(길이 >= MIN_FOLD_LEN) (b) 같은 DOI 인 행끼리 묶는다.
  - 묶음 안에 서로 다른 DOI 가 둘 이상이면 다른 논문일 수 있어 건너뛰고 보고한다.
  - 살아 있는 행 1건 + tombstone 만 있는 묶음은 조치 없이 건수만 보고한다.
  유지할 행(keeper)은 fix_title_subscripts 와 같은 규칙이다: 사람 확인 > 상태 순위(다운완료 우선) > AI 글 종류 판정 보유 > id 작은 행.
  나머지는 store._update_paper 로 {deleted_at: 지금, dup_of: keeper id} 만 바꾼다(status·status_source 는 그대로, change_seq 가 올라가
  동기화 피드가 deleted + dup_of_ref 로 내보내고 공개 CSV 에서는 빠진다. 행은 지우지 않는다).
  다운완료와 다른 상태가 섞인 묶음은 다운완료 행이 keeper 가 된다(상태 순위). 다운완료가 둘인 묶음은 숨긴 행의 다운로드가 남으므로
  Zotero·PDF 중복은 사람이 정리해야 해서 따로 목록으로 낸다(DB 에는 PDF/Zotero 정보가 없고 status_source·gpt_reason·판정 이력만 있다).

사용법 (VM 의 프로젝트 루트에서):
  python dedup_cleanup.py                       # 드라이런(기본): 통계·표본 출력, DB 변경 없음 (읽기 전용 연결)
  python dedup_cleanup.py --db PATH             # 다른 DB 파일(복사본 점검용). 기본은 store.db_path()
  python dedup_cleanup.py --report out.csv      # 숨길 행 전체 목록 CSV
  python dedup_cleanup.py --apply               # 백업(backup_db) 후 한 트랜잭션으로 숨김, 영향받는 월 CSV 재생성
  python dedup_cleanup.py --apply --skip-backup

주의: --apply 는 되돌릴 수 없는 쓰기라 기본으로 백업을 먼저 한다. 재실행해도 안전하다(숨긴 행은 더 이상 정본이 아니다).
"""
import argparse
import collections
import csv
import logging
import random
import sqlite3
import sys

import backup_db
import store
from fix_title_subscripts import KEEPER_RULE_LABELS, KEEPER_RULES, _keeper_key

logger = logging.getLogger(__name__)

DOWNLOADED = "다운완료"


def _components(rows) -> list[list]:
    """살아 있는 정본 행을 title_fold 또는 DOI 가 같으면 한 묶음으로 잇는다(union-find). 2건 이상인 묶음만 반환."""
    parent = {r["id"]: r["id"] for r in rows}

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        parent[find(a)] = find(b)

    seen: dict[tuple, int] = {}
    for r in rows:
        keys = []
        fold = store.title_fold(r["title"])
        if len(fold) >= store.MIN_FOLD_LEN:
            keys.append(("fold", fold))
        doi = store.doi_key(r["url"])
        if doi:
            keys.append(("doi", doi))
        for k in keys:
            if k in seen:
                union(seen[k], r["id"])
            else:
                seen[k] = r["id"]
    comps = collections.defaultdict(list)
    for r in rows:
        comps[find(r["id"])].append(r)
    return [v for v in comps.values() if len(v) > 1]


def _kind(live) -> str:
    """묶음이 왜 묶였는지: 공백만 다름(기존 규칙이 이미 잡던 것) / 구두점·HTML·하이픈 표기 차이 / DOI 만 같음."""
    if len({store.title_fold(r["title"]) for r in live}) > 1:
        return "doi_only"
    if len({r["title_key"].replace(" ", "") for r in live}) == 1:
        return "space_only"
    return "punct_html"


def find_duplicates(rows) -> dict:
    """정리 계획(읽기 전용, rows 는 papers 의 모든 행).

    반환: {"groups": [{"keeper","losers","rows","reason","kind"}], "doi_conflicts": [행 목록], "live_tombstone": 건수}.
    rows(keeper 가 맨 앞)는 묶음의 살아 있는 행 전체다.
    """
    live_rows = [r for r in rows if r["dup_of"] is None and r["deleted_at"] is None]
    groups, doi_conflicts = [], []
    for members in _components(live_rows):
        live = sorted(members, key=_keeper_key)
        if len({d for d in (store.doi_key(r["url"]) for r in live) if d}) > 1:
            doi_conflicts.append(live)
            continue
        keys = [_keeper_key(r) for r in live]
        reason = next((rule for i, rule in enumerate(KEEPER_RULES) if len({k[i] for k in keys}) > 1), "id")
        groups.append({"keeper": live[0], "losers": live[1:], "rows": live, "reason": reason, "kind": _kind(live)})
    # 살아 있는 행 1건 + 같은 title_fold 의 tombstone(정본, 삭제됨) - 조치 없이 건수만
    grouped = {r["id"] for g in groups for r in g["rows"]} | {r["id"] for c in doi_conflicts for r in c}
    tomb_folds = {store.title_fold(r["title"]) for r in rows if r["dup_of"] is None and r["deleted_at"] is not None}
    tomb_folds = {f for f in tomb_folds if len(f) >= store.MIN_FOLD_LEN}
    live_tombstone = len({store.title_fold(r["title"]) for r in live_rows if r["id"] not in grouped} & tomb_folds)
    return {"groups": groups, "doi_conflicts": doi_conflicts, "live_tombstone": live_tombstone}


def plan(conn: sqlite3.Connection) -> dict:
    return find_duplicates(conn.execute("SELECT * FROM papers ORDER BY id").fetchall())


def apply_plan(conn: sqlite3.Connection) -> dict:
    """쓰기 잠금을 잡은 뒤 DB 를 다시 읽어 숨긴다(수집 cron 과 겹쳐도 최신 상태 기준). 예외 시 전부 롤백."""
    with store.write_tx(conn):
        now = store._now()
        dedupe = plan(conn)
        for g in dedupe["groups"]:
            for loser in g["losers"]:
                store._update_paper(conn, loser, {"deleted_at": now, "dup_of": g["keeper"]["id"]}, now=now)
    return dedupe


def hidden_months(dedupe: dict) -> set[str]:
    return {loser["bucket_month"] for g in dedupe["groups"] for loser in g["losers"]}


def _history(conn: sqlite3.Connection, paper_id: int) -> str:
    try:
        rows = conn.execute(
            "SELECT at, old_status, new_status, new_source FROM status_history WHERE paper_id=? ORDER BY seq", (paper_id,)
        ).fetchall()
    except sqlite3.DatabaseError:
        return ""
    return " | ".join(f"{r['at']} {r['old_status']}->{r['new_status']}({r['new_source']})" for r in rows)


def write_report(dedupe: dict, path: str) -> None:
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group_kind", "keeper_reason", "keeper_id", "keeper_status", "hide_id", "hide_status", "hide_status_source",
                    "journal", "hide_created_at", "hide_title", "hide_url"])
        for g in dedupe["groups"]:
            k = g["keeper"]
            for r in g["losers"]:
                w.writerow([g["kind"], g["reason"], k["id"], k["status"], r["id"], r["status"], r["status_source"],
                            r["journal"], r["created_at"], r["title"], r["url"]])


def print_report(dedupe: dict, conn: sqlite3.Connection, applied: bool, sample: int = 10) -> None:
    groups = dedupe["groups"]
    n_rows = sum(len(g["losers"]) for g in groups)
    print(f"\n=== 놓친 중복 정리 ({'적용 결과' if applied else '드라이런'}) ===")
    print(f"숨길 그룹 {len(groups)}개 -> 숨길 행 {n_rows}건 (그룹마다 1건 유지)")
    kinds = collections.Counter(g["kind"] for g in groups)
    print("묶인 이유: 공백만 다름 {} / 구두점·HTML·하이픈 표기 차이 {} / DOI 만 같음 {}".format(
        kinds["space_only"], kinds["punct_html"], kinds["doi_only"]))
    print(f"서로 다른 DOI 라 건너뛴 그룹 {len(dedupe['doi_conflicts'])}개 / 살아 있는 행 1건 + tombstone 인 그룹 {dedupe['live_tombstone']}개 (조치 없음)")
    print("유지 행을 가른 근거: " + ", ".join(f"{KEEPER_RULE_LABELS[r]} {sum(g['reason'] == r for g in groups)}" for r in KEEPER_RULES))
    statuses = collections.Counter("/".join(sorted(r["status"] for r in g["rows"])) for g in groups)
    print("상태 조합: " + ", ".join(f"{k} {n}" for k, n in statuses.most_common()))
    by_journal = [g for g in groups if len({r["journal"] for r in g["rows"]}) > 1]
    print(f"\n저널 표기가 서로 다른 그룹 {len(by_journal)}개" + (":" if by_journal else ""))
    for g in by_journal:
        print("  " + " / ".join(f"id={r['id']} {r['journal']}" for r in g["rows"]) + f"  :: {g['keeper']['title'][:70]}")
    mixed = [g for g in groups if len({r["status"] for r in g["rows"]}) > 1 and any(r["status"] == DOWNLOADED for r in g["rows"])]
    print(f"\n상태가 엇갈려 다운완료 행을 유지하는 그룹 {len(mixed)}개" + (":" if mixed else ""))
    for g in mixed:
        print(f"  {g['keeper']['title'][:100]}")
        for r in g["rows"]:
            print(f"    id={r['id']} status={r['status']} source={r['status_source']} [{'유지' if r is g['keeper'] else '숨김'}]")
    double = [g for g in groups if sum(r["status"] == DOWNLOADED for r in g["rows"]) > 1]
    print(f"\n다운완료가 둘 이상인 그룹 {len(double)}개 (Zotero·PDF 중복은 사람이 확인 - DB 에는 PDF/Zotero 정보가 없다)" + (":" if double else ""))
    for g in double:
        print(f"  {g['keeper']['title'][:100]}")
        for r in g["rows"]:
            print(f"    id={r['id']} [{'유지' if r is g['keeper'] else '숨김'}] source={r['status_source']} created={r['created_at']} "
                  f"journal={r['journal']} reason={r['gpt_reason'][:60]!r}\n      history: {_history(conn, r['id']) or '(없음)'}")
    if groups:
        print("\n표본:")
        for g in random.Random(7).sample(groups, min(sample, len(groups))):
            print(f"  [{g['kind']}] 유지 id={g['keeper']['id']} ({g['reason']}): {g['keeper']['title'][:90]}")
            for loser in g["losers"]:
                print(f"    숨김 id={loser['id']}: {loser['title'][:90]}")
    if dedupe["doi_conflicts"]:
        print("\nDOI 충돌로 건너뛴 그룹 (표본):")
        for live in dedupe["doi_conflicts"][:sample]:
            for r in live:
                print(f"  id={r['id']} doi={store.doi_key(r['url'])}: {r['title'][:90]}")
    by_month = collections.Counter(loser["bucket_month"] for g in groups for loser in g["losers"])
    print("\n숨길 행의 월별: " + (", ".join(f"{m} {n}" for m, n in sorted(by_month.items())) or "(없음)"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="title_fold·DOI 로 놓친 중복 행을 숨긴다 (기본은 드라이런)")
    parser.add_argument("--db", metavar="PATH", help="DB 파일 경로(기본: store.db_path())")
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 변경 (기본은 드라이런)")
    parser.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    parser.add_argument("--report", metavar="PATH", help="숨길 행 전체 목록을 CSV 로 저장")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        conn = store.connect(args.db) if args.apply else store.connect(args.db, readonly=True)
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        dedupe = plan(conn)
        if args.report:
            write_report(dedupe, args.report)
            logger.info("전체 목록 저장: %s", args.report)
        if not args.apply:
            print_report(dedupe, conn, applied=False)
            logger.info("드라이런 완료. 실제 숨김은 --apply 로 실행하세요.")
            return 0
        if not dedupe["groups"]:
            print_report(dedupe, conn, applied=True)
            logger.info("숨길 행이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup(args.db))
        dedupe = apply_plan(conn)
        print_report(dedupe, conn, applied=True)
        for month in sorted(hidden_months(dedupe)):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 숨김은 이미 커밋됨 - CSV 는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("완료: 숨김 %d건", sum(len(g["losers"]) for g in dedupe["groups"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
