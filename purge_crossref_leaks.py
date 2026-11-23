#!/usr/bin/env python
"""Crossref 로 들어온 비논문(편집자 글·행사 공지·논평·Nature/Science 뉴스) 정본 행을 tombstone 하는 일회성 운영 스크립트
(hide_crossref_email_dups.py 와 같은 성격, 2026-11-03 비논문 라벨 정리와 같은 방식: deleted_at).

배경:
  2026-11-23 전 저널을 Crossref 수집으로 바꾼 뒤 유출 실측: ACS 편집자 글(Celebrating·Announcing·Tribute·Interview ...), RSC 편집 글
  (DOI 번호 첫 자리 9), Cell Press 논평(Chem at 10·Reaction·SnapShot), iScience Editor Note, Small "Comment on:", Nature Communications 의
  received/accepted 이력이 없는 해설(0 저자). crossref_source.skip_reason 에 규칙을 넣은 뒤(수집 차단), 이미 DB 에 있는 행을 이 스크립트가 걷어낸다.

대상:
  정본 살아 있는 행(deleted_at·dup_of 없음) 중 URL 이 https://doi.org/ 인 행. DOI 와 제목으로 crossref_source.skip_reason 을 다시 돌려
  TOMBSTONE_REASONS 에 드는 사유(editorial-title / nature-* / science-not-peer-reviewed / correction-title / cover-title)면 대상이다.
  10.1038/ · 10.1126/science. 행은 Crossref assertion(received/accepted) 이 필요하므로 DOI 로 다시 조회한다(--assertions-cache 로 재사용).
  조회하지 못한 행은 건드리지 않고 unverified 로 보고만 한다. short-title / filters 사유는 오탐 여지가 있어 보고만 한다(review).
  사람이 건드린 행(status_source='human' / article_kind_source='human' / status_history.actor 있음)은 tombstone 하지 않고 따로 보고한다.
  AI 판정(ai_article_kind)은 대상 선정에 쓰지 않는다 - AI 판정 숨김은 공개 내보내기에서 따로 처리된다.

처리: store._update_paper 로 deleted_at 을 쓴다(change_seq 가 올라 동기화 피드(deleted=true)와 월 CSV 재생성에 반영). status 는 건드리지 않는다.
  tombstone 도 title_key 를 점유하므로 같은 제목이 다시 들어오지 않는다.

사용법 (프로젝트 루트, 프로젝트 venv):
  python purge_crossref_leaks.py --report leaks.csv                    # 드라이런(기본), DB 변경 없음
  python purge_crossref_leaks.py --assertions-cache nat.json --report leaks.csv
  python purge_crossref_leaks.py --apply --assertions-cache nat.json   # 백업 후 한 트랜잭션으로 tombstone, 월 CSV 재생성
  --skip-backup                                                        # --apply 전 backup_db 백업 생략(방금 백업했을 때)
재실행해도 안전하다(tombstone 된 행은 더 이상 대상이 아니다).
"""
import argparse
import collections
import csv
import json
import logging
import sqlite3
import sys
import urllib.parse

import backup_db
import crossref_source as cs
import store
from hide_crossref_email_dups import _human_touched

logger = logging.getLogger(__name__)

LIVE = "deleted_at IS NULL AND dup_of IS NULL"
TOMBSTONE_REASONS = frozenset({
    "editorial-title", "nature-magazine", "nature-not-peer-reviewed", "science-not-peer-reviewed", "correction-title", "cover-title",
})
REVIEW_STATUSES = ("관련", "다운완료", "불확실")  # 사람·AI 가 관심을 보인 상태 - tombstone 전에 눈으로 확인할 것
ASSERTION_BATCH = 40
_NEEDS_ASSERTION = ("10.1038/", "10.1126/science.")


def row_doi(row) -> str:
    return urllib.parse.unquote(row["url"][len("https://doi.org/"):])


def fetch_assertions(dois: list[str]) -> dict[str, list[str]]:
    """DOI -> assertion 이름 목록(received/accepted 등). Crossref REST 조회(DOI 필터, 40건씩). 실패하면 예외(부분 결과를 쓰지 않는다)."""
    out: dict[str, list[str]] = {}
    for i in range(0, len(dois), ASSERTION_BATCH):
        batch = dois[i:i + ASSERTION_BATCH]
        url = cs._url("/works", {"filter": ",".join("doi:" + d for d in batch), "rows": len(batch), "select": "DOI,assertion"})
        for it in cs._get_json(url)["message"].get("items", []):
            out[it["DOI"].lower()] = [a.get("name") for a in it.get("assertion") or [] if isinstance(a, dict)]
    return out


def load_cache(path: str | None) -> dict[str, list[str]]:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except FileNotFoundError:
        return {}
    # refetch 스크립트 형식({"assert": [...]})과 이 스크립트 형식(목록) 모두 받는다
    return {k.lower(): (v["assert"] if isinstance(v, dict) else v) for k, v in raw.items()}


def collect_assertions(rows, cache_path: str | None, fetch=fetch_assertions) -> dict[str, list[str]]:
    """assertion 이 필요한 행(10.1038/·10.1126/science.)의 DOI 를 캐시에서 채우고 모자란 것만 조회한다. 조회한 결과는 캐시 파일에 합쳐 쓴다."""
    known = load_cache(cache_path)
    need = [row_doi(r) for r in rows if row_doi(r).lower().startswith(_NEEDS_ASSERTION)]
    todo = sorted({d for d in need if d.lower() not in known})
    if todo:
        logger.info("Crossref assertion 조회: %d건 (캐시 %d건)", len(todo), len(need) - len(todo))
        known.update(fetch(todo))
        if cache_path:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(known, f)
    return known


def find_targets(conn: sqlite3.Connection, assertions: dict[str, list[str]]) -> dict:
    """대상 계획(읽기 전용). 반환: tombstone / human / review / unverified 목록(각 항목 {row, reason})."""
    plan = {"tombstone": [], "human": [], "review": [], "unverified": []}
    for r in conn.execute(f"SELECT * FROM papers WHERE {LIVE} AND url LIKE 'https://doi.org/%' ORDER BY id"):
        doi = row_doi(r)
        item = {"DOI": doi, "type": "journal-article"}
        if doi.lower().startswith(_NEEDS_ASSERTION):
            names = assertions.get(doi.lower())
            if names is None:
                if not doi.lower().startswith("10.1038/d41586-"):  # d41586 은 assertion 없이도 사유가 정해진다
                    plan["unverified"].append({"row": r, "reason": "assertion-missing"})
                    continue
                names = []
            item["assertion"] = [{"name": n} for n in names]
        reason = cs.skip_reason(item, r["title"])
        if reason is None:
            continue
        if reason not in TOMBSTONE_REASONS:
            plan["review"].append({"row": r, "reason": reason})
        elif _human_touched(conn, r):
            plan["human"].append({"row": r, "reason": reason})
        else:
            plan["tombstone"].append({"row": r, "reason": reason})
    return plan


def apply_plan(conn: sqlite3.Connection, assertions: dict[str, list[str]]) -> dict:
    """쓰기 잠금을 잡은 뒤 대상을 다시 고르고 tombstone 한다(한 트랜잭션, 예외 시 전부 롤백). 반환: 적용한 계획."""
    with store.write_tx(conn):
        plan = find_targets(conn, assertions)
        now = store._now()
        for t in plan["tombstone"]:
            store._update_paper(conn, t["row"], {"deleted_at": now}, now=now)
    return plan


def write_report(plan: dict, path: str) -> None:
    cols = ["action", "reason", "id", "journal", "status", "status_source", "ai_article_kind", "created_at", "title", "url"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        for action in ("tombstone", "human", "review", "unverified"):
            for t in plan[action]:
                r = t["row"]
                w.writerow([action, t["reason"], r["id"], r["journal"], r["status"], r["status_source"], r["ai_article_kind"] or "",
                            r["created_at"], r["title"], r["url"]])


def print_report(plan: dict, applied: bool, sample: int = 12) -> None:
    tomb = plan["tombstone"]
    print(f"\n=== Crossref 비논문 정리 ({'적용 결과' if applied else '드라이런'}) ===")
    print(f"tombstone {len(tomb)}건 / 사람이 건드려 보존 {len(plan['human'])} / 규칙 밖 검토만 {len(plan['review'])} / assertion 미확인 {len(plan['unverified'])}")
    print("사유: " + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(t["reason"] for t in tomb).items())))
    print("상태: " + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(t["row"]["status"] for t in tomb).items())))
    flagged = [t for t in tomb if t["row"]["status"] in REVIEW_STATUSES]
    if flagged:
        print(f"주의: 상태가 {'/'.join(REVIEW_STATUSES)} 인 행 {len(flagged)}건 포함")
        for t in flagged[:sample]:
            print(f"  id={t['row']['id']} [{t['row']['status']}] {t['row']['title'][:100]}")
    print("\n저널별:")
    for j, n in collections.Counter(t["row"]["journal"] for t in tomb).most_common():
        print(f"  {n:5d}  {j}")
    for name in ("human", "review"):
        for t in plan[name][:sample]:
            print(f"  [{name}/{t['reason']}] id={t['row']['id']}: {t['row']['title'][:100]}")
    print(f"\n영향받는 월: {', '.join(sorted({t['row']['bucket_month'] for t in tomb})) or '(없음)'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Crossref 비논문 정본 행을 tombstone 한다 (기본은 드라이런)",
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--apply", action="store_true", help="실제로 DB 를 고친다 (기본은 드라이런)")
    p.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    p.add_argument("--report", metavar="PATH", help="전체 목록을 이 CSV 로 저장")
    p.add_argument("--assertions-cache", metavar="PATH", help="DOI -> assertion JSON. 있으면 재사용하고 모자란 DOI 만 조회해 합쳐 쓴다")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        conn = store.connect() if args.apply else store.connect(readonly=True)
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        logger.info("DB: %s", store.db_path())
        rows = conn.execute(f"SELECT url FROM papers WHERE {LIVE} AND url LIKE 'https://doi.org/%'").fetchall()
        assertions = collect_assertions(rows, args.assertions_cache)
        plan = find_targets(conn, assertions)
        if args.report:
            write_report(plan, args.report)
            logger.info("전체 목록 저장: %s", args.report)
        if not args.apply:
            print_report(plan, applied=False)
            logger.info("드라이런 완료. 실제 tombstone 은 --apply 로 실행하세요.")
            return 0
        if not plan["tombstone"]:
            print_report(plan, applied=True)
            logger.info("정리할 행이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        plan = apply_plan(conn, assertions)
        print_report(plan, applied=True)
        if args.report:
            write_report(plan, args.report)  # 잠금 후 다시 고른 목록으로 덮어쓴다
        for month in sorted({t["row"]["bucket_month"] for t in plan["tombstone"]}):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # tombstone 은 이미 커밋됨 - CSV 는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("완료: tombstone %d건", len(plan["tombstone"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
