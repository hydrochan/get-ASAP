#!/usr/bin/env python
"""Crossref 수집으로 새로 들어온 행과 같은 논문인 기존 메일 행을 숨기는 일회성 운영 스크립트 (fix_title_subscripts.py 와 같은 성격).

배경:
  저널을 메일 수집에서 Crossref 수집(ingest)으로 바꾸면, Crossref 행의 제목이 기존 메일 행과 공백·기호·초록 덧붙임 때문에
  정확히 같지 않아 엄격한 중복 판정(crossref_source.partition_items)이 놓친 쌍이 생긴다(ACS Central Science 는 메일 제목 뒤에
  초록이 붙어 있다). 이 스크립트가 그런 쌍을 느슨하게 찾아 메일 행을 숨기고 Crossref 행을 남긴다.

대상:
  - 새 행(Crossref): created_at >= --since-created, 살아 있는 정본 행(deleted_at·dup_of 없음), URL 이 https://doi.org/ 이고
    저널이 crossref_journals.json 의 ingest 저널(별칭 포함)인 행. --since-created 는 Crossref 백필을 시작하기 직전 시각으로 준다
    (그 뒤의 ingest 저널 행은 메일 경로가 막혀 있으므로 Crossref 행뿐이다).
  - 옛 행(메일): created_at < --since-created, 같은 저널(별칭은 한 저널로 본다)의 살아 있는 정본 행. 기간 제한은 없다.
  - 짝 판정(match): doi(DOI 가 같다) > alnum(영숫자 키가 같다) > prefix(짧은 쪽 >= 25자가 긴 쪽의 접두 - 초록 덧붙임)
    > fuzzy(유사도 >= 0.9, crossref_source.loose_match). 두 행의 DOI 가 둘 다 있고 다르면 다른 논문이므로 짝으로 보지 않는다
    (doi_conflict 로 보고만 한다). prefix·fuzzy 는 "Part I"/"Part II" 처럼 로마자·숫자 몇 글자만 다른 쌍을 오병합으로 보고 거절한다.
  - 옛 행 하나에 새 행이 둘 이상 붙으면 판정 순위가 높은 쪽(그다음 id 가 작은 쪽) 하나만 잇고 나머지는 ambiguous 로 보고만 한다.

처리 (keeper 규칙은 fix_title_subscripts 의 "사람 확인이 최우선"을 따르되, 새 행은 항상 '대기중'이라 상태 순위·AI 판정은 비교하지 않는다):
  - 기본: 옛 메일 행을 숨긴다(deleted_at + dup_of = 새 행 id). status·status_source 는 건드리지 않는다.
  - 옛 행을 사람이 건드렸으면(status_source='human' / article_kind_source='human' / 사람 판정 이력 status_history.actor 있음) 반대로
    새 Crossref 행을 숨긴다(dup_of = 옛 행 id). 사람의 결정을 지키기 위해서다.
  store._update_paper 로 쓰므로 change_seq 가 올라가 동기화 피드(deleted=true + dup_of_ref)와 월 CSV 재생성에 반영된다.

사용법 (프로젝트 루트에서, 프로젝트 venv):
  python hide_crossref_email_dups.py --since-created 2026-11-23T09:00:00Z                 # 드라이런(기본), DB 변경 없음
  python hide_crossref_email_dups.py --since-created 2026-11-23T09:00:00Z --report pairs.csv   # 짝 전체 목록 CSV
  python hide_crossref_email_dups.py --since-created 2026-11-23T09:00:00Z --apply         # 백업 후 한 트랜잭션으로 숨김
  --journals "ACS Central Science" PCCP      # 저널 이름(대소문자 무시)으로 거르기
  --skip-backup                              # --apply 전에 backup_db 백업을 생략(방금 백업했을 때)

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다. 쓰기 잠금을 잡은 뒤 짝을 다시 고르므로
  수집 cron 과 겹쳐도 최신 상태 기준이다. 재실행해도 안전하다(숨긴 행은 더 이상 대상이 아니다).
"""
import argparse
import bisect
import collections
import csv
import difflib
import logging
import re
import sqlite3
import sys

import backup_db
import crossref_source as cs
import store

logger = logging.getLogger(__name__)

MATCH_RANK = {"doi": 0, "alnum": 1, "prefix": 2, "fuzzy": 3}
_SERIES_RE = re.compile(r"^[ivx0-9]{1,4}$")  # "Part I" -> "Part II", "(1)" -> "(2)" 같은 연재·번호 표지 차이
MIN_PREFIX_REMAINDER = 4  # 접두 일치에서 긴 쪽이 이 글자 수 이상 더 길어야 초록 덧붙임으로 본다(그보다 짧으면 번호 표지 차이일 수 있다)
LIVE = "deleted_at IS NULL AND dup_of IS NULL"


def _since_arg(value: str) -> str:
    """YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SSZ. created_at 문자열 비교용으로 그대로 쓴다."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) or re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        return value
    raise argparse.ArgumentTypeError(f"YYYY-MM-DD 또는 YYYY-MM-DDTHH:MM:SSZ 형식이어야 합니다: {value!r}")


def _series_only_difference(a: str, b: str) -> bool:
    """두 영숫자 키가 로마자·숫자 몇 글자만 다른가(Part I/II, 1/2 등 다른 논문일 가능성이 큰 쌍)."""
    ops = [op for op in difflib.SequenceMatcher(None, a, b, autojunk=False).get_opcodes() if op[0] != "equal"]
    if not ops:
        return False
    return all(
        _SERIES_RE.match(a[i1:i2] or "0") and _SERIES_RE.match(b[j1:j2] or "0") and max(i2 - i1, j2 - j1) <= 4
        for _, i1, i2, j1, j2 in ops
    )


def title_match(new_title: str, old_title: str) -> str | None:
    """두 제목이 같은 논문으로 보이는가: "alnum" / "prefix" / "fuzzy" / None. 순수 함수."""
    a, b = cs.alnum_key(new_title), cs.alnum_key(old_title)
    if not a or not b:
        return None
    if a == b:
        return "alnum"
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= cs.PREFIX_MIN_LEN and long_.startswith(short):
        return "prefix" if len(long_) - len(short) >= MIN_PREFIX_REMAINDER else None
    if cs.loose_match(a, b) and not _series_only_difference(a, b):
        return "fuzzy"
    return None


class _OldIndex:
    """저널 한 곳의 옛 행 조회표: DOI·영숫자 키 정확 조회, 접두 후보(정렬 키 bisect), 길이 순 정렬(유사도 후보)."""

    def __init__(self, rows):
        self.by_doi: dict[str, list] = collections.defaultdict(list)
        self.by_key: dict[str, list] = collections.defaultdict(list)
        for r in rows:
            doi = (store.doi_from_url(r["url"]) or "").casefold()
            if doi:
                self.by_doi[doi].append(r)
            k = cs.alnum_key(r["title"])
            if k:
                self.by_key[k].append(r)
        self.sorted_keys = sorted(self.by_key)
        self.by_len = sorted(((len(k), k) for k in self.by_key))
        self.lens = [n for n, _ in self.by_len]

    def candidates(self, title: str):
        """title 과 느슨하게 닿을 수 있는 옛 행 후보(중복 없이)."""
        a = cs.alnum_key(title)
        if not a:
            return []
        seen: dict[int, object] = {}
        # (1) 옛 키가 a 로 시작: 초록이 붙은 메일 제목
        if len(a) >= cs.PREFIX_MIN_LEN:
            lo = bisect.bisect_left(self.sorted_keys, a)
            for k in self.sorted_keys[lo:]:
                if not k.startswith(a):
                    break
                for r in self.by_key[k]:
                    seen[r["id"]] = r
        # (2) a 가 옛 키로 시작: 옛 키가 더 짧다(긴 쪽이 새 행)
        for n in range(cs.PREFIX_MIN_LEN, len(a) + 1):
            for r in self.by_key.get(a[:n], ()):
                seen[r["id"]] = r
        # (3) 길이가 비슷한 키(유사도): 짧은 쪽 >= 0.9 * 긴 쪽
        lo = bisect.bisect_left(self.lens, int(len(a) * cs.FUZZY_RATIO))
        hi = bisect.bisect_right(self.lens, int(len(a) / cs.FUZZY_RATIO) + 1)
        for _, k in self.by_len[lo:hi]:
            for r in self.by_key[k]:
                seen[r["id"]] = r
        return list(seen.values())


def _human_touched(conn: sqlite3.Connection, row) -> bool:
    if row["status_source"] == "human" or row["article_kind_source"] == "human":
        return True
    return conn.execute(
        "SELECT 1 FROM status_history WHERE paper_id=? AND actor IS NOT NULL LIMIT 1", (row["id"],)
    ).fetchone() is not None


def _canonical_map(config: dict) -> dict[str, str]:
    """DB 에 저장될 수 있는 저널 이름(정식명·별칭, casefold) -> 조회 대상 정식명. ingest 저널만."""
    out = {}
    for e in cs.journal_entries(config, mode="ingest"):
        for name in [e["journal"]] + cs.alias_names(config, e["journal"]):
            out[name.casefold()] = e["journal"]
    return out


def find_pairs(conn: sqlite3.Connection, config: dict, since_created: str, journals: list[str] | None = None) -> dict:
    """짝 계획(읽기 전용). 반환: {"pairs": [...], "doi_conflicts": [...], "ambiguous": [...]}.

    pair: {"action": hide_email|hide_crossref, "match", "journal", "new", "old"} (new/old 는 papers 행).
    """
    canon = _canonical_map(config)
    wanted = {j.strip().casefold() for j in journals} if journals else None
    new_by_journal: dict[str, list] = collections.defaultdict(list)
    old_by_journal: dict[str, list] = collections.defaultdict(list)
    for r in conn.execute(f"SELECT * FROM papers WHERE {LIVE} ORDER BY id"):
        j = canon.get((r["journal"] or "").casefold())
        if j is None or (wanted is not None and j.casefold() not in wanted):
            continue
        if r["created_at"] >= since_created:
            if r["url"].startswith("https://doi.org/"):
                new_by_journal[j].append(r)
        else:
            old_by_journal[j].append(r)

    found, doi_conflicts = [], []
    for journal, news in new_by_journal.items():
        index = _OldIndex(old_by_journal.get(journal, []))
        for new in news:
            new_doi = (store.doi_from_url(new["url"]) or "").casefold()
            hits: dict[int, tuple[str, object]] = {}
            for r in index.by_doi.get(new_doi, ()) if new_doi else ():
                hits[r["id"]] = ("doi", r)
            for old in index.candidates(new["title"]):
                if old["id"] in hits:
                    continue
                kind = title_match(new["title"], old["title"])
                if kind is None:
                    continue
                old_doi = (store.doi_from_url(old["url"]) or "").casefold()
                if new_doi and old_doi and new_doi != old_doi:
                    doi_conflicts.append({"journal": journal, "match": kind, "new": new, "old": old})
                    continue
                hits[old["id"]] = (kind, old)
            for kind, old in hits.values():
                found.append({"match": kind, "journal": journal, "new": new, "old": old})

    found.sort(key=lambda p: (MATCH_RANK[p["match"]], p["new"]["id"], p["old"]["id"]))
    pairs, ambiguous, used_old = [], [], set()
    for p in found:
        if p["old"]["id"] in used_old:
            ambiguous.append(p)
            continue
        used_old.add(p["old"]["id"])
        p["action"] = "hide_crossref" if _human_touched(conn, p["old"]) else "hide_email"
        pairs.append(p)
    pairs.sort(key=lambda p: (p["journal"], p["new"]["id"], p["old"]["id"]))
    return {"pairs": pairs, "doi_conflicts": doi_conflicts, "ambiguous": ambiguous}


def apply_pairs(conn: sqlite3.Connection, config: dict, since_created: str, journals: list[str] | None = None) -> dict:
    """쓰기 잠금을 잡은 뒤 짝을 다시 골라 숨긴다(한 트랜잭션, 예외 시 전부 롤백). 반환: 적용한 계획."""
    with store.write_tx(conn):
        plan = find_pairs(conn, config, since_created, journals)
        now = store._now()
        for p in plan["pairs"]:
            loser, keeper = (p["old"], p["new"]) if p["action"] == "hide_email" else (p["new"], p["old"])
            store._update_paper(conn, loser, {"deleted_at": now, "dup_of": keeper["id"]}, now=now)
    return plan


def hidden_months(plan: dict) -> set[str]:
    return {(p["old"] if p["action"] == "hide_email" else p["new"])["bucket_month"] for p in plan["pairs"]}


def write_report(plan: dict, path: str) -> None:
    """짝 전체 목록 CSV(Excel 에서 열리도록 BOM 포함). action 은 hide_email / hide_crossref / skip_doi_conflict / skip_ambiguous."""
    cols = ["action", "journal", "match", "new_id", "new_title", "new_url", "old_id", "old_title", "old_url",
            "old_status", "old_status_source", "old_ai_article_kind"]
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(cols)
        groups = [(p["action"], p) for p in plan["pairs"]] + [("skip_doi_conflict", p) for p in plan["doi_conflicts"]] \
            + [("skip_ambiguous", p) for p in plan["ambiguous"]]
        for action, p in groups:
            n, o = p["new"], p["old"]
            w.writerow([action, p["journal"], p["match"], n["id"], n["title"], n["url"], o["id"], o["title"], o["url"],
                        o["status"], o["status_source"], o["ai_article_kind"] or ""])


def print_report(plan: dict, applied: bool, sample: int = 15) -> None:
    pairs = plan["pairs"]
    print(f"\n=== Crossref 행과 겹치는 메일 행 정리 ({'적용 결과' if applied else '드라이런'}) ===")
    by_action = collections.Counter(p["action"] for p in pairs)
    print(f"짝 {len(pairs)}쌍: 메일 행 숨김 {by_action['hide_email']}, Crossref 행 숨김(사람이 건드린 메일 행 보존) {by_action['hide_crossref']}")
    print(f"건너뜀: DOI 가 달라 다른 논문으로 본 쌍 {len(plan['doi_conflicts'])}, 옛 행이 중복 배정이라 보류 {len(plan['ambiguous'])}")
    print("판정 종류: " + ", ".join(f"{k} {v}" for k, v in sorted(collections.Counter(p['match'] for p in pairs).items())))
    print("숨기는 메일 행의 상태: " + ", ".join(
        f"{k} {v}" for k, v in sorted(collections.Counter(p["old"]["status"] for p in pairs if p["action"] == "hide_email").items())))
    print("\n저널별:")
    for j, n in collections.Counter(p["journal"] for p in pairs).most_common():
        print(f"  {n:5d}  {j}")
    if pairs:
        print("\n표본:")
        step = max(1, len(pairs) // sample)
        for p in pairs[::step][:sample]:
            print(f"  [{p['match']}/{p['action']}] {p['journal']}\n    new id={p['new']['id']}: {p['new']['title'][:110]}\n"
                  f"    old id={p['old']['id']}: {p['old']['title'][:110]}")
    print(f"\n영향받는 월: {', '.join(sorted(hidden_months(plan))) or '(없음)'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Crossref 행과 같은 논문인 기존 메일 행을 숨긴다 (기본은 드라이런)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--since-created", type=_since_arg, required=True, metavar="TS",
                        help="Crossref 백필 시작 직전 시각(UTC). 이 시각 이후 생성된 doi.org 행이 새 행, 이전 행이 옛 행")
    parser.add_argument("--apply", action="store_true", help="실제로 DB 를 고친다 (기본은 드라이런)")
    parser.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    parser.add_argument("--report", metavar="PATH", help="짝 전체 목록을 이 CSV 파일로 저장")
    parser.add_argument("--journals", nargs="+", metavar="NAME", help="이 저널만(대소문자 무시)")
    parser.add_argument("--config", default=None, help="crossref_journals.json 경로(기본: 프로젝트 루트)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    config = cs.load_config(args.config)
    try:
        conn = store.connect() if args.apply else store.connect(readonly=True)
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    names = [n.strip() for v in args.journals or [] for n in v.split(",") if n.strip()] or None
    try:
        logger.info("DB: %s", store.db_path())
        plan = find_pairs(conn, config, args.since_created, names)
        if args.report:
            write_report(plan, args.report)
            logger.info("전체 목록 저장: %s", args.report)
        if not args.apply:
            print_report(plan, applied=False)
            logger.info("드라이런 완료. 실제 숨김은 --apply 로 실행하세요.")
            return 0
        if not plan["pairs"]:
            print_report(plan, applied=True)
            logger.info("숨길 짝이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        plan = apply_pairs(conn, config, args.since_created, names)
        print_report(plan, applied=True)
        if args.report:
            write_report(plan, args.report)  # 적용 시점(잠금 후)에 다시 고른 목록으로 덮어쓴다
        for month in sorted(hidden_months(plan)):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 숨김은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("완료: 숨김 %d건", len(plan["pairs"]))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
