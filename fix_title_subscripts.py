#!/usr/bin/env python
"""위·아래첨자가 쪼개져 저장된 기존 제목 정정 스크립트 (일회성 운영 스크립트, fix_future_dates.py 와 같은 성격).

배경:
  예전 파서는 get_text(separator=" ")로 제목을 뽑아 CO<sub>2</sub> 를 "CO 2", Zn<sup>2+</sup> 를 "Zn 2+" 로 저장했다.
  파서는 parsers.base.title_text 로 고쳤지만(그 전에 저장된 행은 그대로 남음) 이 스크립트가 기존 행을 정정한다.
  메일 원문이 남아 있지 않으므로 화학식 규칙(원소 기호 + 아래첨자/전하, 동위원소 접두, 단위 지수, 이탤릭 접두)으로
  "원소 기호 뒤에 떨어져 나간 첨자"만 다시 붙인다. "Phase 2", "In 2025", "at 4 K" 같은 일반 문구는 건드리지 않는다.

대상:
  모든 행(정본·tombstone·dup 행 모두). 새 제목의 title_key 가 다른 정본 행(dup_of IS NULL, tombstone 포함)의 키와
  같아지면 충돌이다 - 정본 행끼리는 title_key 가 유일해야 하므로 그 행은 건너뛰고 보고만 한다. 백필 대상끼리 충돌하면
  (살아 있는 행 우선, 그다음 id 가 작은 행) 하나만 정정하고 나머지는 건너뛴다.

정정:
  title 과 title_key 를 store._update_paper 로 바꾼다 -> change_seq·updated_at 이 올라가므로
  (1) PC 동기화 피드 /api/sync/v1/changes 가 변경분을 받고 (2) export_dirty()가 해당 월 CSV를 다시 쓴다.

사용법 (VM의 프로젝트 루트에서):
  python fix_title_subscripts.py                         # 드라이런(기본): 저널별 건수·충돌·표본 출력, DB 변경 없음
  python fix_title_subscripts.py --report out.csv        # 전체 목록(id, 저널, 옛 제목, 새 제목, 충돌)을 CSV 로 저장
  python fix_title_subscripts.py --apply                 # 백업(backup_db) 후 한 트랜잭션으로 정정 + CSV 재생성
  python fix_title_subscripts.py --apply --skip-backup   # 방금 백업했다면 백업 생략

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다.
  재실행해도 안전하다(정정된 제목은 더 이상 대상이 아니다. 충돌로 건너뛴 행만 계속 보고된다).
"""
import argparse
import collections
import csv
import logging
import random
import re
import sqlite3
import sys

import backup_db
import store

logger = logging.getLogger(__name__)

ELEMENTS = set("""H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu Zn Ga Ge As Se Br Kr
Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re
Os Ir Pt Au Hg Tl Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm Bk Cf Es Fm Md No Lr""".split())
_SYM = re.compile(r"[A-Z][a-z]?")
# 원소 기호처럼 보이지만 일반 단어·약어라 첨자를 받지 않는 토큰
STOP = {"In", "As", "At", "Be", "He", "No", "I", "ON", "OFF", "NIH", "CIS", "CNN", "HIV", "ICU", "CHO", "CYP", "NO.", "BCS", "PS"}
ISOTOPES = {
    "H": {1, 2, 3}, "C": {11, 13, 14}, "N": {14, 15}, "O": {16, 17, 18}, "F": {18, 19}, "P": {31, 32}, "B": {10, 11},
    "Li": {6, 7}, "Na": {22, 23}, "Al": {27}, "Si": {29}, "Fe": {57}, "Cu": {64, 67}, "Ga": {67, 68}, "Zr": {89},
    "In": {111}, "Tc": {99}, "Lu": {177}, "I": {123, 124, 125, 131}, "Tl": {201, 205}, "Xe": {129}, "Pt": {195},
    "Sn": {119}, "Se": {77}, "Cd": {111, 113}, "Y": {89, 90}, "Ac": {225}, "At": {211}, "Ra": {223}, "Pb": {203, 212},
    "Bi": {213}, "Sr": {89, 90}, "Cs": {137}, "U": {235, 238}, "Pu": {239}, "Th": {232}, "Mo": {95, 99}, "Sm": {153},
    "Ho": {166}, "Co": {57, 59, 60}, "Mn": {55}, "Zn": {65, 67}, "S": {33, 34, 35}, "Cl": {35, 36, 37}, "Mg": {25},
    "Rb": {87}, "Ba": {133}, "Ge": {68}, "V": {51}, "Hg": {199, 201}, "Te": {125}, "Ag": {107, 109}, "Ni": {61},
}
UNITS = {"cm", "m", "g", "s", "h", "L", "mol", "kg", "mg", "mL", "A", "mA", "W", "V", "K", "M", "nm", "mm", "J", "kJ",
         "Pa", "kPa", "MPa", "ppm", "min", "μm", "µm", "mW", "kW", "Wh", "mAh", "F", "S", "Ω", "lm", "cd", "yr", "d"}
ITALIC_PREFIX = {"situ", "Situ", "vivo", "vitro", "closo", "nido", "arachno", "ortho", "meta", "para", "Ortho", "Meta",
                 "Para", "cis", "trans", "Cis", "Trans", "tert", "sec", "iso", "neo", "endo", "exo", "syn", "anti", "ipso",
                 "operando", "Operando", "silico", "ex"}

_FTOK = re.compile(r"((?:[A-Z][a-z]?\d*(?:\.\d+)?)+)$")  # 왼쪽 토큰 끝의 화학식 덩어리
_SUB = re.compile(r"^(\d{1,3}(?:\.\d{1,2})?(?:[-−–](?:[xyzδ]|\d(?:\.\d+)?))?|[xyzδ]|[xyzδ][-−–]?\d?)(?P<tail>.*)$")
_CHG = re.compile(r"^(\d{0,2}[+⁺]|\d{1,2}[−–\-⁻]|[−⁻]|δ[+−\-]|[+−]\d?)(?P<tail>.*)$")
_TAIL_OK = re.compile(r"^(?:$|[,.:;)\]/@·′'″]|[-‐‑–—][\w(\[]|[-−–](?=$))")


def _left_formula(tok: str, allow_stop: bool = False) -> bool:
    """왼쪽 토큰이 (시작/비영숫자 뒤의) 올바른 화학식 덩어리로 끝나거나 닫는 괄호로 끝나는가."""
    if tok.endswith((")", "]")) and len(tok) > 1:
        return True
    m = _FTOK.search(tok)
    if not m:
        return False
    start = m.start(1)
    if start > 0 and tok[start - 1].isalnum():
        return False  # 영숫자 덩어리 전체가 화학식이어야 한다 ("Ortho" 는 "O"+"rtho" 가 아니다)
    return _formula_ok_all(m.group(1), allow_stop=allow_stop or start > 0)


def _formula_ok_all(run: str, allow_stop: bool = False) -> bool:
    letters = re.sub(r"[\d.]", "", run)
    if letters in STOP and not allow_stop:
        return False
    syms = _SYM.findall(letters)
    return bool(syms) and "".join(syms) == letters and all(x in ELEMENTS for x in syms)


def _ends_with_sub(tok: str) -> bool:
    """토큰이 원소+숫자 / 전하 / x 로 끝나는가 = 방금 첨자를 붙였다."""
    return bool(re.search(r"(?:[A-Z][a-z]?|[)\]])(?:\d+(?:\.\d+)?|[xyzδ])(?:[+−\-–⁺⁻]|\d?[+−–])?$|[+−⁺⁻]$", tok))


def rejoin(title: str) -> str:
    """쪼개진 첨자를 다시 붙인 제목. 순수 함수(입출력 없음). 바꿀 게 없으면 공백만 정리한 제목을 돌려준다.

    한 번 훑은 결과가 다시 훑을 때 더 바뀌는 드문 경우("Ti 3 C 2 T x /PPy": 말단기 Tx 가 마지막에야 붙어서 "/" 를 못 붙임)가
    있어, 더 바뀌지 않을 때까지 반복한다 -> 재실행해도 결과가 같다(멱등).
    """
    t = re.sub(r"\s+", " ", title).strip()
    for _ in range(5):
        nxt = _rejoin_once(t)
        if nxt == t:
            break
        t = nxt
    return t


def _rejoin_once(title: str) -> str:
    t = re.sub(r"\s+", " ", title).strip()
    # 괄호: "( ii )" -> "(ii)", "[ B" -> "[B"
    t = re.sub(r"([(\[]) ", r"\1", t)
    t = re.sub(r" ([)\]])", r"\1", t)
    # 이탤릭 글자 뒤 프라임: "N ′" -> "N′"
    t = re.sub(r"(?<=\S) (?=[′″])", "", t)
    toks = t.split(" ")
    out = [toks[0]] if toks else []
    for i in range(1, len(toks)):
        left, right = out[-1], toks[i]
        join = False
        # 1) 화학식 + 아래첨자 / 전하
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        stop_ok = bool(re.match(r"^(?:[A-Z][a-z]?)+(?:\d|$|[/)\]])", nxt)) and _formula_ok_all(re.match(r"^((?:[A-Z][a-z]?)+)", nxt).group(1), True) if re.match(r"^[A-Z]", nxt) else False
        if _left_formula(left, allow_stop=stop_ok or bool(re.search(r"\d$", out[-2] if len(out) > 1 else ""))):
            closing = left.endswith((")", "]"))
            for mt in (_SUB, _CHG):
                m = mt.match(right)
                if not (m and _TAIL_OK.match(m.group("tail"))):
                    continue
                # 닫는 괄호 뒤의 전하는 숫자가 있을 때만 붙인다: "[Fe(CN)6] 3−" 는 붙이고 "(R32) + trans" 는 그대로 둔다
                if mt is _CHG and closing and not re.search(r"\d", m.group(1)):
                    continue
                join = True
                break
        # 2) 첨자가 붙은 화학식 + 전하 ("MoO4" + "2–", "NO3" + "−")
        if not join and _ends_with_sub(left):
            m = _CHG.match(right)
            if m and _TAIL_OK.match(m.group("tail")):
                join = True
        # 3) 사슬: "Al2" + "O3"/"O3:" / "SnO4" (오른쪽이 첨자 달린 화학식)
        if not join and re.search(r"[A-Z][a-z]?\d+(?:\.\d+)?$|[)\]]\d+$", left):
            m = re.match(r"^((?:[A-Z][a-z]?\d*(?:\.\d+)?)+)(.*)$", right)
            if m and re.search(r"\d", m.group(1)) and _formula_ok_all(m.group(1)) and _TAIL_OK.match(m.group(2)):
                join = True
            # 다음 토큰이 첨자를 받을 맨 원소로 이어지는 경우: "Al2" + "O" + "3"
            elif m and not m.group(2) and _formula_ok_all(m.group(1), True) and i + 1 < len(toks) and _SUB.match(toks[i + 1]) \
                    and _TAIL_OK.match(_SUB.match(toks[i + 1]).group("tail")):
                join = True
        # 3b) 열린 아래첨자 식 "3−" + "δ"/"x"
        if not join and re.search(r"\d[−\-–]$", left):
            m = re.match(r"^[xyzδ](?P<tail>.*)$", right)
            if m and _TAIL_OK.match(m.group("tail")):
                join = True
        # 4) 첨자 뒤: "-단어", "/X", "@X", ",", ":", ";" 붙이기
        if not join and _ends_with_sub(left) and re.match(r"^(?:[-‐‑–—][\w(\[]|[/@,:;·])", right):
            join = True
        # 5) 동위원소 접두 "13 C", "111 In-Labeled", "1 H,"
        if not join and re.fullmatch(r"\d{1,3}", left):
            m = re.match(r"^([A-Z][a-z]?)(?=$|[-‐,;:)/ ]|NMR)", right)
            if m and int(left) in ISOTOPES.get(m.group(1), ()):
                join = True
        # 6) 단위 지수 "cm −1", "g −1"
        if not join and left in UNITS and re.fullmatch(r"[−-]\d[,.;:)]?", right):
            join = True
        # 7) 이탤릭 한 글자 / 이탤릭 접두 + "-단어" ("n -Butyl", "closo -hydro", "N ,")
        if not join and (re.fullmatch(r"[A-Za-zα-ωΑ-Ω]", left) or left in ITALIC_PREFIX) and re.match(r"^[-‐‑][A-Za-z(]", right):
            join = True
        if not join and re.fullmatch(r"[A-Za-z]", left) and right.startswith(","):
            join = True
        if join:
            out[-1] = left + right
        else:
            out.append(right)
    t = " ".join(out)
    # MXene 말단기 "Ti3C2 Tx" / "Nb2CT x"
    t = re.sub(r"(?<=C)(\d?) ?T ?([xz])\b", r"\1T\2", t)
    t = re.sub(r"\bPM (2\.5|10)\b", r"PM\1", t)
    return t


def find_targets(conn: sqlite3.Connection) -> list[dict]:
    """정정 대상 행의 계획 목록(읽기 전용). 각 계획의 collision 은 None / "existing" / "intra".

    - existing: 새 title_key 가 다른 정본 행(dup_of IS NULL, tombstone 포함)의 현재 키와 같다.
    - intra: 백필 대상끼리 새 키가 같다(살아 있는 행 우선, 그다음 id 가 작은 행 하나만 정정 대상으로 남는다).
    dup 행(dup_of IS NOT NULL)은 title_key 유일 인덱스 밖이라 충돌 검사를 하지 않는다.
    """
    rows = conn.execute("SELECT * FROM papers ORDER BY id").fetchall()
    owners = collections.defaultdict(list)  # 현재 title_key -> 정본 행 id
    for r in rows:
        if r["dup_of"] is None:
            owners[r["title_key"]].append(r["id"])
    plans = []
    for r in rows:
        old = re.sub(r"\s+", " ", r["title"]).strip()
        new = rejoin(r["title"])
        if new == old:
            continue
        new_key = store.normalize_title(new)
        collision = None
        if r["dup_of"] is None and any(i != r["id"] for i in owners.get(new_key, ())):
            collision = "existing"
        plans.append({
            "row": r, "id": r["id"], "journal": r["journal"], "old_title": r["title"], "new_title": new,
            "new_key": new_key, "collision": collision,
        })
    groups = collections.defaultdict(list)
    for p in plans:
        if p["row"]["dup_of"] is None and p["collision"] is None:
            groups[p["new_key"]].append(p)
    for group in groups.values():
        group.sort(key=lambda p: (p["row"]["deleted_at"] is not None, p["id"]))
        for p in group[1:]:
            p["collision"] = "intra"
    return plans


def apply_fix(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """충돌이 아닌 대상을 한 트랜잭션으로 정정한다. 반환: (적용한 계획, 충돌로 건너뛴 계획).

    대상은 쓰기 잠금(BEGIN IMMEDIATE)을 잡은 뒤 다시 고른다(수집 cron과 겹쳐도 최신 상태 기준).
    중간에 예외가 나면 전부 롤백된다.
    """
    with store.write_tx(conn):
        plans = find_targets(conn)
        now = store._now()
        applied, skipped = [], []
        for plan in plans:
            if plan["collision"]:
                skipped.append(plan)
            elif store._update_paper(conn, plan["row"], {"title": plan["new_title"]}, now=now):
                applied.append(plan)
    return applied, skipped


def affected_months(plans: list[dict]) -> list[str]:
    return sorted({p["row"]["bucket_month"] for p in plans})


def write_report(plans: list[dict], path: str) -> None:
    """전체 목록 CSV(Excel 에서 열리도록 BOM 포함): id, journal, old_title, new_title, collision."""
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "journal", "old_title", "new_title", "collision"])
        for p in plans:
            w.writerow([p["id"], p["journal"], p["old_title"], p["new_title"], p["collision"] or ""])


def print_report(plans: list[dict], applied: bool, sample: int = 15, top_journals: int = 40) -> None:
    n_existing = sum(p["collision"] == "existing" for p in plans)
    n_intra = sum(p["collision"] == "intra" for p in plans)
    ok = [p for p in plans if not p["collision"]]
    print(f"\n=== 쪼개진 첨자 제목 정정 ({'적용 결과' if applied else '드라이런'}) ===")
    print(f"대상 {len(plans)}건 = 정정 {len(ok)}건 + 충돌로 건너뜀 {n_existing + n_intra}건 (기존 행과 충돌 {n_existing}, 백필끼리 충돌 {n_intra})")
    by_journal = collections.Counter((p["journal"] or "(저널 없음)") for p in plans)
    print(f"\n저널별 건수 (상위 {min(top_journals, len(by_journal))}/{len(by_journal)}개):")
    for journal, n in by_journal.most_common(top_journals):
        print(f"  {n:6d}  {journal}")
    if plans:
        print("\n표본:")
        for p in random.Random(7).sample(plans, min(sample, len(plans))):
            tag = f"  [충돌:{p['collision']}]" if p["collision"] else ""
            print(f"  id={p['id']}{tag}\n    - {p['old_title'][:110]}\n    + {p['new_title'][:110]}")
    print(f"\n영향받는 월: {', '.join(affected_months(ok)) or '(없음)'}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="위·아래첨자가 쪼개져 저장된 제목을 다시 붙인다 (기본은 드라이런)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--apply", action="store_true", help="실제로 DB를 정정 (기본은 드라이런)")
    parser.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    parser.add_argument("--report", metavar="PATH", help="전체 대상 목록을 이 CSV 파일로 저장")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        conn = store.connect() if args.apply else store.connect(readonly=True)
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        plans = find_targets(conn)
        if args.report:
            write_report(plans, args.report)
            logger.info("전체 목록 저장: %s (%d건)", args.report, len(plans))
        if not args.apply:
            print_report(plans, applied=False)
            logger.info("드라이런 완료. 실제 정정은 --apply 로 실행하세요.")
            return 0
        if not any(not p["collision"] for p in plans):
            print_report(plans, applied=True)
            logger.info("정정할 행이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        applied, skipped = apply_fix(conn)
        print_report(applied + skipped, applied=True)
        for month in affected_months(applied):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 정정은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info("정정 완료: %d건 (충돌로 건너뜀 %d건)", len(applied), len(skipped))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
