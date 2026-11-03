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

중복 정리 (제목 정정보다 먼저, 같은 쓰기 트랜잭션 안에서):
  같은 논문이 쪼개진 제목과 붙은 제목으로 두 번 들어와 정본 행이 둘 있는 경우("Nb 2 O 5 -Filled" 와 "Nb2O5-Filled")를 정리한다.
  정본 행(dup_of IS NULL)을 title_key 에서 공백을 모두 뺀 값(store._TITLE_SQUASH_EXPR 과 같은 키)으로 묶고, 살아 있는
  (deleted_at IS NULL) 행이 2건 이상인 그룹만 대상으로 한다. 그룹 안에서 살아 있는 행만 다루고 tombstone 은 건드리지 않는다.
  - 살아 있는 행 1건 + tombstone 만 있는 그룹은 조치 없이 건수만 보고한다.
  - 살아 있는 두 행의 DOI(store.doi_from_url)가 둘 다 비어 있지 않은데 서로 다르면 다른 논문일 수 있으므로 그룹째 건너뛰고 보고한다.
  유지할 행(keeper)은 다음 순서로 고른다.
    1) status_source == 'human' (사람이 확인한 행)
    2) 상태 순위: 다운완료 < 관련 < 불확실 < 무관 < 실패 < 대기중 (앞쪽이 우선)
    3) ai_article_kind 가 NULL 이 아닌 행 (이미 받은 AI 판정 보존)
    4) id 가 작은 행 (먼저 들어온 행)
  나머지 행은 store._update_paper 로 {deleted_at: 지금, dup_of: keeper id} 만 바꾼다. status·status_source 는 건드리지 않고,
  change_seq 가 올라가 동기화 피드가 deleted=true + dup_of_ref 로 내보내며(paper_autodown 은 삭제 행을 건너뛴다) 공개 export 는
  deleted_at 이 있는 행을 뺀다. 숨긴 행은 dup_of 가 생겨 title_key 유일 인덱스에서 빠지므로, 이어지는 제목 정정에서 같은 키를
  놓고 충돌하지 않는다(숨긴 행의 제목도 다른 dup 행처럼 정정된다). 이전에 충돌로 건너뛰던 keeper 도 이제 정정된다.

사용법 (VM의 프로젝트 루트에서):
  python fix_title_subscripts.py                         # 드라이런(기본): 중복 정리 + 제목 정정 통계·표본 출력, DB 변경 없음
  python fix_title_subscripts.py --report out.csv        # 전체 목록을 CSV 로 저장 (action 열: hide_dup / fix_title, hide_dup 은 keeper_id)
  python fix_title_subscripts.py --apply                 # 백업(backup_db) 후 한 트랜잭션으로 중복 정리 + 정정, 두 단계의 월 CSV 재생성
  python fix_title_subscripts.py --apply --skip-backup   # 방금 백업했다면 백업 생략

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다.
  재실행해도 안전하다(숨긴 행은 더 이상 정본이 아니고 정정된 제목은 더 이상 대상이 아니다. DOI 충돌·충돌로 건너뛴 행만 계속 보고된다).
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


STATUS_RANK = {s: i for i, s in enumerate(("다운완료", "관련", "불확실", "무관", "실패", "대기중"))}  # 낮을수록 우선
KEEPER_RULES = ("human", "status", "verdict", "id")  # _keeper_key 튜플의 각 칸이 결정한 근거 이름
KEEPER_RULE_LABELS = {
    "human": "human(사람 확인)", "status": "상태 순위", "verdict": "AI 글 종류 판정 보유", "id": "id(먼저 들어온 행)",
}


def _verdict(r) -> str | None:
    """AI 글 종류 판정(ai_article_kind). 열이 아직 없는 구 DB 는 판정이 없는 것으로 본다."""
    return r["ai_article_kind"] if "ai_article_kind" in r.keys() else None


def _keeper_key(r) -> tuple:
    """작을수록 유지 우선: (사람 확인 아님, 상태 순위, AI 판정 없음, id)."""
    return (r["status_source"] != "human", STATUS_RANK.get(r["status"], len(STATUS_RANK)), _verdict(r) is None, r["id"])


def find_duplicates(rows) -> dict:
    """공백만 다른 정본 행 그룹의 정리 계획(읽기 전용, rows 는 papers 의 모든 행).

    반환: {"groups": [{"keeper", "losers", "rows", "reason"}], "doi_conflicts": [살아 있는 행 목록], "live_tombstone": 건수}.
    groups 는 실제로 숨길 그룹이고 rows 는 keeper 가 맨 앞인 살아 있는 행 전체, reason 은 KEEPER_RULES 중 keeper 를 가른 첫 근거다.
    """
    by_squash = collections.defaultdict(list)
    for r in rows:
        if r["dup_of"] is None:
            by_squash[r["title_key"].replace(" ", "")].append(r)
    groups, doi_conflicts, live_tombstone = [], [], 0
    for members in by_squash.values():
        live = sorted((r for r in members if r["deleted_at"] is None), key=_keeper_key)
        if len(live) < 2:
            if len(live) == 1 and len(members) > 1:
                live_tombstone += 1
            continue
        if len({d.casefold() for d in (store.doi_from_url(r["url"]) for r in live) if d}) > 1:
            doi_conflicts.append(live)
            continue
        keys = [_keeper_key(r) for r in live]
        reason = next(rule for i, rule in enumerate(KEEPER_RULES) if len({k[i] for k in keys}) > 1)
        groups.append({"keeper": live[0], "losers": live[1:], "rows": live, "reason": reason})
    return {"groups": groups, "doi_conflicts": doi_conflicts, "live_tombstone": live_tombstone}


def simulate_hides(rows, dedupe: dict) -> list[dict]:
    """드라이런용: 중복 정리를 적용한 뒤의 행 목록(DB 는 건드리지 않는다). 숨길 행에 deleted_at·dup_of 를 단다."""
    hidden = {loser["id"]: g["keeper"]["id"] for g in dedupe["groups"] for loser in g["losers"]}
    out = []
    for r in rows:
        d = dict(r)
        if d["id"] in hidden:
            d["deleted_at"], d["dup_of"] = store._now(), hidden[d["id"]]
        out.append(d)
    return out


def plan_all(conn: sqlite3.Connection) -> tuple[dict, list[dict]]:
    """(중복 정리 계획, 중복 정리를 반영한 상태에서의 제목 정정 계획). 읽기 전용 - 드라이런·보고용."""
    rows = conn.execute("SELECT * FROM papers ORDER BY id").fetchall()
    dedupe = find_duplicates(rows)
    return dedupe, find_targets(conn, simulate_hides(rows, dedupe))


def find_targets(conn: sqlite3.Connection, rows=None) -> list[dict]:
    """정정 대상 행의 계획 목록(읽기 전용). 각 계획의 collision 은 None / "existing" / "intra".

    - existing: 새 title_key 가 다른 정본 행(dup_of IS NULL, tombstone 포함)의 현재 키와 같다.
    - intra: 백필 대상끼리 새 키가 같다(살아 있는 행 우선, 그다음 id 가 작은 행 하나만 정정 대상으로 남는다).
    dup 행(dup_of IS NOT NULL)은 title_key 유일 인덱스 밖이라 충돌 검사를 하지 않는다.
    rows 를 주면 DB 대신 그 행들(simulate_hides 결과)을 기준으로 계획한다.
    """
    if rows is None:
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


def _hide_duplicates(conn: sqlite3.Connection, now: str) -> dict:
    """중복 그룹의 keeper 가 아닌 살아 있는 행을 숨긴다(deleted_at + dup_of). write_tx 안에서 호출한다.
    status·status_source 는 바꾸지 않는다(status 가 changes 에 없으므로 _update_paper 가 status_source 를 요구하지 않는다)."""
    dedupe = find_duplicates(conn.execute("SELECT * FROM papers ORDER BY id").fetchall())
    for g in dedupe["groups"]:
        for loser in g["losers"]:
            store._update_paper(conn, loser, {"deleted_at": now, "dup_of": g["keeper"]["id"]}, now=now)
    return dedupe


def _fix_titles(conn: sqlite3.Connection, now: str) -> tuple[list[dict], list[dict]]:
    """충돌이 아닌 제목 정정 대상을 정정한다(현재 DB 상태 기준). write_tx 안에서 호출한다."""
    applied, skipped = [], []
    for plan in find_targets(conn):
        if plan["collision"]:
            skipped.append(plan)
        elif store._update_paper(conn, plan["row"], {"title": plan["new_title"]}, now=now):
            applied.append(plan)
    return applied, skipped


def apply_fix(conn: sqlite3.Connection) -> tuple[list[dict], list[dict]]:
    """제목 정정만 한 트랜잭션으로 한다(중복 정리 없음). 반환: (적용한 계획, 충돌로 건너뛴 계획).

    대상은 쓰기 잠금(BEGIN IMMEDIATE)을 잡은 뒤 다시 고른다(수집 cron과 겹쳐도 최신 상태 기준).
    중간에 예외가 나면 전부 롤백된다. 명령(main)은 중복 정리까지 하는 apply_all 을 쓴다.
    """
    with store.write_tx(conn):
        return _fix_titles(conn, store._now())


def apply_all(conn: sqlite3.Connection) -> tuple[dict, list[dict], list[dict]]:
    """중복 정리 -> (정리가 반영된 상태에서) 제목 정정을 한 트랜잭션으로 한다. 반환: (중복 정리 계획, 적용한 계획, 건너뛴 계획).

    두 단계 모두 쓰기 잠금을 잡은 뒤 DB 를 다시 읽어 고른다. 중간에 예외가 나면 전부 롤백된다.
    """
    with store.write_tx(conn):
        now = store._now()
        dedupe = _hide_duplicates(conn, now)
        applied, skipped = _fix_titles(conn, now)
    return dedupe, applied, skipped


def affected_months(plans: list[dict]) -> list[str]:
    return sorted({p["row"]["bucket_month"] for p in plans})


def hidden_months(dedupe: dict) -> set[str]:
    """숨긴 행이 있는 월 - 그 월의 공개 CSV 에서 숨긴 행이 빠져야 한다(keeper 의 월은 제목 정정 계획이 따로 챙긴다)."""
    return {loser["bucket_month"] for g in dedupe["groups"] for loser in g["losers"]}


def write_report(plans: list[dict], path: str, dedupe: dict | None = None) -> None:
    """전체 목록 CSV(Excel 에서 열리도록 BOM 포함): id, journal, old_title, new_title, collision, action, keeper_id.

    action 은 hide_dup(중복 정리: 숨길 행, keeper_id 가 남길 행) 또는 fix_title(제목 정정). hide_dup 행이 먼저 나온다.
    """
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["id", "journal", "old_title", "new_title", "collision", "action", "keeper_id"])
        for g in (dedupe or {"groups": []})["groups"]:
            for loser in g["losers"]:
                w.writerow([loser["id"], loser["journal"], loser["title"], "", "", "hide_dup", g["keeper"]["id"]])
        for p in plans:
            w.writerow([p["id"], p["journal"], p["old_title"], p["new_title"], p["collision"] or "", "fix_title", ""])


def print_dedupe_report(dedupe: dict, applied: bool, sample: int = 10) -> None:
    groups = dedupe["groups"]
    print(f"\n=== 공백만 다른 중복 정리 ({'적용 결과' if applied else '드라이런'}) ===")
    print(f"숨길 그룹 {len(groups)}개 -> 숨길 행 {sum(len(g['losers']) for g in groups)}건 (그룹마다 1건 유지)")
    print(f"DOI 가 달라 건너뛴 그룹 {len(dedupe['doi_conflicts'])}개")
    print(f"살아 있는 행 1건 + tombstone 인 그룹 {dedupe['live_tombstone']}개 (조치 없음)")
    reasons = collections.Counter(g["reason"] for g in groups)
    print("유지 행을 가른 근거: " + ", ".join(f"{KEEPER_RULE_LABELS[r]} {reasons[r]}" for r in KEEPER_RULES))
    differ = [g for g in groups if len({r["status"] for r in g["rows"]}) > 1]
    print(f"\n상태가 서로 다른 그룹 {len(differ)}개" + (":" if differ else ""))
    for g in differ:
        print(f"  {g['keeper']['title'][:100]}")
        for r in g["rows"]:
            print(f"    id={r['id']} status={r['status']} status_source={r['status_source']} [{'유지' if r is g['keeper'] else '숨김'}]")
    if groups:
        print("\n표본:")
        for g in random.Random(7).sample(groups, min(sample, len(groups))):
            print(f"  유지 id={g['keeper']['id']} ({g['reason']}): {g['keeper']['title'][:100]}")
            for loser in g["losers"]:
                print(f"    숨김 id={loser['id']}: {loser['title'][:100]}")
    if dedupe["doi_conflicts"]:
        print("\nDOI 충돌로 건너뛴 그룹 (표본):")
        for live in dedupe["doi_conflicts"][:sample]:
            for r in live:
                print(f"  id={r['id']} doi={store.doi_from_url(r['url'])}: {r['title'][:100]}")
    print(f"\n영향받는 월: {', '.join(sorted(hidden_months(dedupe))) or '(없음)'}")


def print_report(plans: list[dict], applied: bool, sample: int = 15, top_journals: int = 40) -> None:
    n_existing = sum(p["collision"] == "existing" for p in plans)
    n_intra = sum(p["collision"] == "intra" for p in plans)
    ok = [p for p in plans if not p["collision"]]
    print(f"\n=== 쪼개진 첨자 제목 정정 ({'적용 결과' if applied else '드라이런, 중복 정리를 반영한 뒤 기준'}) ===")
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
        dedupe, plans = plan_all(conn)
        if args.report:
            write_report(plans, args.report, dedupe)
            n_hide = sum(len(g["losers"]) for g in dedupe["groups"])
            logger.info("전체 목록 저장: %s (숨김 %d건 + 제목 정정 %d건)", args.report, n_hide, len(plans))
        if not args.apply:
            print_dedupe_report(dedupe, applied=False)
            print_report(plans, applied=False)
            logger.info("드라이런 완료. 실제 정리·정정은 --apply 로 실행하세요.")
            return 0
        if not dedupe["groups"] and not any(not p["collision"] for p in plans):
            print_dedupe_report(dedupe, applied=True)
            print_report(plans, applied=True)
            logger.info("정리·정정할 행이 없어 종료합니다.")
            return 0
        if not args.skip_backup:
            logger.info("백업 생성: %s", backup_db.make_backup())
        dedupe, applied, skipped = apply_all(conn)
        print_dedupe_report(dedupe, applied=True)
        print_report(applied + skipped, applied=True)
        for month in sorted(set(affected_months(applied)) | hidden_months(dedupe)):
            try:
                store.export_csv(month, conn)
            except Exception as e:  # 정리·정정은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
                logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
                return 1
        logger.info(
            "완료: 중복 숨김 %d건, 제목 정정 %d건 (충돌로 건너뜀 %d건)",
            sum(len(g["losers"]) for g in dedupe["groups"]), len(applied), len(skipped),
        )
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
