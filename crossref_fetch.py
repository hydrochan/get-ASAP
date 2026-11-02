#!/usr/bin/env python
"""Crossref 수집 CLI (운영 스크립트, fix_future_dates.py 와 같은 성격). 규칙·중복 판정은 crossref_source.py 참고.

배경:
  Nature Communications·iScience·PCCP 는 출판사가 알림 메일을 잘라 보내 메일만으로는 대부분 빠진다. Crossref 는 DOI 를
  메일보다 먼저 등록하므로 이 저널들은 Crossref 에서 직접 채운다(ingest). 나머지 저널은 DB 에 쓰지 않고 Crossref 대비
  메일 포착률·지연만 잰다(shadow) - 메일을 전부 Crossref 로 바꿔도 되는지 판단할 근거다.
  저널·ISSN·mode 는 crossref_journals.json 에 있다.

모드:
  ingest (기본)  mode=ingest 저널의 새 DOI 를 가져와 DB 에 넣는다. 드라이런이 기본이고 --apply 일 때만 쓴다.
                 조회 시작일: --since 가 있으면 그것, 없으면 저널별 커서(meta `crossref_cursor:<issn>`, 겹침 2일),
                 둘 다 없으면 56일 전. 커서는 --apply 때 훑은 항목의 created 최댓값으로 앞으로만 옮긴다.
  shadow         읽기 전용(DB 에 쓰지 않는다). mode=shadow 저널마다 [--until-(--days-1), --until] 구간의 Crossref 연구 논문을
                 DB 행과 대조해 저널별 crossref_count / matched_in_db / missing_in_db / 포착률 / 지연 중앙값(DB created_at -
                 Crossref created, lag_d) / 메일에만 있는 행(email_only) / 메일 도착 지연(arr_lag_d)을 표로 내고
                 logs/crossref_shadow.jsonl 에 한 줄 덧붙인다. 지표 정의는 shadow_compare 독스트링 참고.
                 --until 기본값은 3일 전(메일이 도착할 시간을 준다).
                 해석 주의(2026-11 실측): ACS 는 메일이 Crossref 보다 중앙값 약 4일 늦어 기본 구간 끝(3일 전)에서는 최근 며칠분이
                 아직 안 와 포착률이 낮게 나온다(우측 절단) - ACS 대형 6개 저널은 --until 을 10~14일 전으로 잡으면 99~100%.
                 RSC·Applied Energy·IJHE 는 이슈 단위 메일이라 DOI 등록 후 몇 주~몇 달 뒤에 도착해 구간 안 포착률이 0 에 가깝다 -
                 이런 저널은 --margin-days 120 으로 arr_lag_d(도착 지연)를 본다. Nature·Science 는 Crossref 가 뉴스·해설도
                 journal-article 로 등록해 연구 논문만 가려낼 수 없으므로 분모가 부풀려진다.

사용법 (프로젝트 루트에서, 프로젝트 venv):
  python crossref_fetch.py                                   # 드라이런: ingest 대상 저널별 건수만 출력
  python crossref_fetch.py ingest --since 2026-09-07         # 드라이런, 조회 시작일 지정
  python crossref_fetch.py ingest --apply                    # 백업(backup_db) 후 저장 + 커서 전진 + 영향받은 월 CSV 재생성
  python crossref_fetch.py ingest --apply --skip-backup      # 방금 백업했다면 백업 생략
  python crossref_fetch.py shadow --days 14                  # 포착률 측정(읽기 전용)
  python crossref_fetch.py shadow --until 2026-10-29         # 오래된 DB 사본 대상이면 구간 끝을 직접 지정
  --journals "Nature Communications" iScience               # 저널 이름(대소문자 무시)으로 거르기
  --debug                                                   # 요청·묶음·소요 시간을 DEBUG 로 출력

속도: Crossref 공개 풀(public)은 동시 요청 1건 / 초당 5건이고 요청 하나가 4~13초 걸린다(응답 크기와 무관, select 로 이미 33KB).
  그래서 shadow 는 저널 30개씩을 한 질의(issn: 필터 OR)로 묶어 요청 수를 ~85 -> ~10 으로 줄이고, 동시 요청 수는 첫 응답의
  X-Concurrency-Limit 에 맞춘다(--workers 상한 4). CROSSREF_MAILTO 를 설정하면 polite 풀(동시 3)로 더 빨라진다.

환경변수: CROSSREF_MAILTO (선택) - 있으면 요청에 mailto 를 붙여 Crossref polite pool 을 쓴다. 코드에는 이메일을 두지 않는다.

주의:
  --apply 는 되돌릴 수 없는 쓰기이므로 기본으로 backup_db.make_backup()을 먼저 실행한다(새로 넣을 논문이 있을 때만).
  재실행해도 안전하다(이미 있는 제목은 건너뛴다). 커서 저장 전에 죽어도 다음 실행이 겹쳐 훑고 중복은 DB 가 걸러낸다.
"""
import argparse
import datetime as dt
import json
import logging
import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor

import backup_db
import crossref_source as cs
import store

logger = logging.getLogger(__name__)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
SHADOW_LOG = os.path.join(PROJECT_ROOT, "logs", "crossref_shadow.jsonl")  # logs/ 는 .gitignore 대상

DEFAULT_SINCE_DAYS = 56        # 커서도 --since 도 없을 때 ingest 가 거슬러 올라가는 기간
SHADOW_DEFAULT_DAYS = 14
SHADOW_END_LAG_DAYS = 3        # shadow 구간 끝 = 오늘 - 3일
SHADOW_MARGIN_DAYS = 14        # 구간 앞쪽 이 기간의 Crossref 항목도 가져와 DB 행(email_only·도착 지연)과 대조만 한다. 메일이 한참 늦는 저널은 --margin-days 로 늘린다
SAMPLE_TITLES = 5
SHADOW_BATCH = 30              # 한 번의 Crossref 질의에 묶는 저널 수(issn: 필터 OR). 요청 수 ~85 -> ~10
DEFAULT_WORKERS = 4            # 동시 요청 상한. 실제로는 풀이 알려주는 X-Concurrency-Limit(public 1, polite 3)을 넘지 않는다


def _date_arg(value: str) -> dt.date:
    try:
        return dt.date.fromisoformat(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"YYYY-MM-DD 형식이어야 합니다: {value!r}")


def _split_names(values) -> list[str] | None:
    if not values:
        return None
    return [n.strip() for v in values for n in v.split(",") if n.strip()]


def _table(headers: list[str], rows: list[list], left: int = 1) -> str:
    """고정폭 표. 앞쪽 left 개 열은 왼쪽 정렬, 나머지는 오른쪽 정렬."""
    cells = [[str(c) for c in r] for r in rows]
    widths = [max(len(h), *(len(r[i]) for r in cells)) if cells else len(h) for i, h in enumerate(headers)]

    def fmt(r):
        return "  ".join(c.ljust(w) if i < left else c.rjust(w) for i, (c, w) in enumerate(zip(r, widths)))

    return "\n".join([fmt(headers), "  ".join("-" * w for w in widths)] + [fmt(r) for r in cells])


def _lag_days(db_created: str, cr_created: str | None) -> float | None:
    """DB created_at - Crossref created (일). 해석 불가면 None. 양수 = 메일(DB)이 Crossref 보다 늦다."""
    if not db_created or not cr_created:
        return None
    try:
        a = dt.datetime.fromisoformat(db_created.replace("Z", "+00:00"))
        b = dt.datetime.fromisoformat(cr_created.replace("Z", "+00:00"))
    except ValueError:
        return None
    if a.tzinfo is None:
        a = a.replace(tzinfo=dt.timezone.utc)
    if b.tzinfo is None:
        b = b.replace(tzinfo=dt.timezone.utc)
    return (a - b).total_seconds() / 86400


def _median(values: list[float]) -> float | None:
    return round(statistics.median(values), 2) if values else None


# ---------- ingest ----------

def plan_ingest(conn, config: dict, entry: dict, *, since: dt.date | None, today: dt.date) -> dict:
    """저널 한 곳의 조회 시작일을 정하고 Crossref 에서 가져와 분류한다(DB 읽기만)."""
    issn = entry["issn"][0]
    cursor = cs.get_cursor(conn, issn)
    if since is not None:
        from_date, source = since.isoformat(), "--since"
    elif cursor:
        from_date, source = cs.cursor_from_date(cursor), f"커서 {cursor}"
    else:
        from_date, source = (today - dt.timedelta(days=DEFAULT_SINCE_DAYS)).isoformat(), f"기본 {DEFAULT_SINCE_DAYS}일 전"
    names = [entry["journal"]] + cs.alias_names(config, entry["journal"])
    index = cs.load_index(
        conn, names, global_exact=True,
        since_created=(today - dt.timedelta(days=cs.DEDUP_WINDOW_DAYS)).isoformat(),
    )
    items = cs.fetch_works(issn, from_date)
    return {
        "journal": entry["journal"], "issn": issn, "from_date": from_date, "source": source,
        "partition": cs.partition_items(items, entry["journal"], index, today),
    }


def print_ingest_report(plans: list[dict], apply: bool, verbose: bool) -> None:
    print(f"\n=== Crossref 수집 ({'--apply: 아래 신규를 저장' if apply else '드라이런: DB 변경 없음'}) ===")
    rows = []
    for p in plans:
        part = p["partition"]
        rows.append([p["journal"], p["issn"], p["from_date"], part.fetched, len(part.non_research), len(part.in_db),
                     len(part.batch_dup), len(part.new)])
    totals = ["합계", "", ""] + [sum(r[i] for r in rows) for i in range(3, 8)]
    print(_table(["journal", "issn", "from", "fetched", "non_research", "in_db", "batch_dup", "new"], rows + [totals], left=3))
    for p in plans:
        part = p["partition"]
        limit = None if verbose else SAMPLE_TITLES
        print(f"\n[{p['journal']}] 조회 시작: {p['from_date']} ({p['source']}), 커서 전진 예정: {part.max_created or '(변화 없음)'}")
        reasons: dict[str, list[str]] = {}
        for reason, title in part.non_research:
            reasons.setdefault(reason, []).append(title)
        groups = [("신규", [x.title for x in part.new])] + [(f"비논문 제외[{k}]", v) for k, v in sorted(reasons.items())]
        for label, titles in groups:
            if titles:
                print(f"  {label} {len(titles)}건" + (f" (앞 {limit}건)" if limit and len(titles) > limit else ""))
                for t in titles[:limit]:
                    print(f"    - {t[:110]}")


def run_ingest(args, conn, config: dict, today: dt.date) -> int:
    entries = cs.journal_entries(config, mode="ingest", names=_split_names(args.journals))
    if not entries:
        print("대상 저널이 없습니다(mode=ingest 이고 --journals 에 해당하는 저널).", file=sys.stderr)
        return 1
    plans, failed = [], 0
    for entry in entries:
        if not entry.get("issn"):
            logger.warning("ISSN 이 없어 건너뜀: %s", entry["journal"])
            failed += 1
            continue
        try:
            started = time.monotonic()
            plans.append(plan_ingest(conn, config, entry, since=args.since, today=today))
            logger.debug("%s: 조회·분류 %.1f초", entry["journal"], time.monotonic() - started)
        except Exception as e:  # 한 저널의 네트워크 오류가 다른 저널 수집을 막지 않게 한다
            logger.error("Crossref 조회 실패 (%s): %s", entry["journal"], e)
            failed += 1
    print_ingest_report(plans, apply=args.apply, verbose=args.verbose)
    if not args.apply:
        logger.info("드라이런 완료. 실제 저장은 --apply 로 실행하세요.")
        return 1 if failed else 0

    new_total = sum(len(p["partition"].new) for p in plans)
    if new_total and not args.skip_backup:
        logger.info("백업 생성: %s", backup_db.make_backup())
    months: set[str] = set()
    saved = skipped = bad = 0
    for p in plans:
        part = p["partition"]
        if part.new:
            result = store.save_papers(part.new, conn)  # 한 트랜잭션. 저장이 끝난 뒤에만 커서를 옮긴다
            saved, skipped, bad = saved + result["saved"], skipped + result["skipped"], bad + result["failed"]
            months |= {store.bucket_month_for(x.date) for x in part.new}
            if result["failed"]:
                logger.warning("저장 실패 %d건 (%s)", result["failed"], p["journal"])
        if part.max_created:
            cs.set_cursor(conn, p["issn"], part.max_created)
    for month in sorted(months):
        try:
            store.export_csv(month, conn)
        except Exception as e:  # 저장은 이미 커밋됨 - CSV는 수동으로 다시 쓰면 된다
            logger.error("CSV 재생성 실패 (%s): %s -> python refresh_csv.py --month %s", month, e, month)
            return 1
    logger.info("수집 완료: 저장 %d / 건너뜀 %d / 실패 %d, 영향받은 월: %s", saved, skipped, bad, ", ".join(sorted(months)) or "(없음)")
    return 1 if (failed or bad) else 0


# ---------- shadow ----------

def shadow_compare(conn, journal: str, names: list[str], items: list[dict], start: dt.date, end: dt.date) -> dict:
    """Crossref 항목(구간 앞 여유분 포함)을 DB 행과 대조해 저널 한 곳의 포착 통계를 낸다(읽기 전용).

    crossref_count 등은 created 가 [start, end] 인 연구 논문만 센다. 구간 앞 여유분은 DB 행 쪽 판정에만 쓴다:
      - email_only: created_at 이 [start, end] 인 DB 행 중 여유분까지 포함한 Crossref 항목에 제목이 없는 행.
        DOI 가 여유 기간보다 더 앞에 등록된 행(이슈 단위로 한참 뒤에 오는 RSC·Applied Energy 메일)도 여기 잡히므로,
        이런 저널은 --margin-days 를 늘려야 진짜 메일 전용만 남는다.
      - median_arrival_lag_days: 같은 DB 행 중 Crossref 와 일치한 것의 (DB created_at - Crossref created) 중앙값.
        median_lag_days 는 Crossref 쪽 구간 기준이라 아직 메일이 안 온 항목이 빠져 짧게 나오고(우측 절단),
        도착 지연은 DB 행 기준이라 그 편향이 없다.
    """
    s, e = start.isoformat(), end.isoformat()
    every = cs.TitleIndex()  # 비논문까지 포함한 Crossref 전체 키 - 메일 전용 행 판정용
    in_window: list[tuple[dict, str]] = []
    non_research = 0
    for item in items:
        title = cs.item_title(item)
        if title:
            every.add_title(title, cs.created_datetime(item) or "")
        day = cs.created_date(item)
        inside = bool(day) and s <= day <= e
        if not cs.is_research_item(item, title):
            non_research += inside
        elif inside:
            in_window.append((item, title))

    db_index = cs.load_index(conn, names, global_exact=False, since_created=None)
    matched, lags, missing = 0, [], []
    for item, title in in_window:
        hit = db_index.lookup(title)
        if hit is None:
            missing.append(title)
            continue
        matched += 1
        lag = _lag_days(hit, cs.created_datetime(item))
        if lag is not None:
            lags.append(lag)

    marks = ",".join("?" * len(names))
    window_rows = conn.execute(
        f"SELECT title_key, created_at FROM papers WHERE journal IN ({marks}) AND deleted_at IS NULL AND dup_of IS NULL "
        "AND substr(created_at, 1, 10) BETWEEN ? AND ?",
        [*names, s, e],
    ).fetchall()
    email_only, arrival = 0, []
    for key, created_at in window_rows:
        cr_created = every.lookup_key(key)
        if cr_created is None:
            email_only += 1
        else:
            lag = _lag_days(created_at, cr_created or None)
            if lag is not None:
                arrival.append(lag)
    baseline_from = (end - dt.timedelta(days=cs.DEDUP_WINDOW_DAYS)).isoformat()
    baseline_rows = conn.execute(
        f"SELECT COUNT(*) FROM papers WHERE journal IN ({marks}) AND deleted_at IS NULL AND created_at >= ?",
        [*names, baseline_from],
    ).fetchone()[0]
    count = len(in_window)
    return {
        "journal": journal,
        "crossref_count": count,
        "matched_in_db": matched,
        "missing_in_db": count - matched,
        "coverage_pct": round(100 * matched / count, 1) if count else None,
        "median_lag_days": _median(lags),
        "median_arrival_lag_days": _median(arrival),
        "non_research_skipped": non_research,
        "db_rows_in_window": len(window_rows),
        "email_only": email_only,
        "baseline_active": baseline_rows > 0,  # 최근 180일에 이 저널 행이 하나도 없으면 메일 기준선이 없다(구독·파싱 안 됨)
        "missing_samples": missing[:3],
        "_lags": lags,
        "_arrival": arrival,
    }


def _aggregate(rows: list[dict]) -> dict:
    count = sum(r["crossref_count"] for r in rows)
    matched = sum(r["matched_in_db"] for r in rows)
    lags = [x for r in rows for x in r["_lags"]]
    arrival = [x for r in rows for x in r["_arrival"]]
    return {
        "journals": len(rows),
        "crossref_count": count,
        "matched_in_db": matched,
        "coverage_pct": round(100 * matched / count, 1) if count else None,
        "median_lag_days": _median(lags),
        "median_arrival_lag_days": _median(arrival),
        "email_only": sum(r["email_only"] for r in rows),
    }


def _fmt(v) -> str:
    return "-" if v is None else str(v)


def fetch_shadow(entries: list[dict], fetch_from: dt.date, end: dt.date, requested_workers: int, batch_size: int) -> dict:
    """저널들을 batch_size 개씩 묶어 Crossref 에서 가져온다. 반환: {저널명: (항목 목록|None, 예외|None)}.

    첫 묶음은 혼자 보내 풀이 허용하는 동시 요청 수(X-Concurrency-Limit)를 알아낸 뒤, 나머지는 min(요청 수, 그 한도)개씩
    동시에 보낸다(public pool 은 1이라 4개를 동시에 보내면 429 만 쌓인다 - 2026-11-02 실측). 묶음 하나가 실패하면 그 묶음의
    저널만 오류로 표시하고 나머지는 계속한다.
    """
    entries = sorted(entries, key=lambda e: e["journal"])
    batches = [entries[i:i + batch_size] for i in range(0, len(entries), batch_size)]

    def run(batch):
        started = time.monotonic()
        issns = sorted({i for e in batch for i in e["issn"]})
        try:
            items = cs.fetch_works_many(issns, fetch_from.isoformat(), end.isoformat())
        except Exception as ex:  # 한 묶음의 오류가 전체를 막지 않게 한다
            logger.debug("묶음 실패 %.1f초 (%d저널): %s", time.monotonic() - started, len(batch), ex)
            return {e["journal"]: (None, ex) for e in batch}
        owners: dict[str, list[str]] = {}
        for e in batch:
            for i in e["issn"]:
                owners.setdefault(i.upper(), []).append(e["journal"])
        per: dict[str, dict] = {e["journal"]: {} for e in batch}
        for n, item in enumerate(items):
            for issn in item.get("ISSN") or []:
                for name in owners.get(str(issn).upper(), []):
                    per[name][item.get("DOI") or f"#{n}"] = item  # 인쇄·전자 ISSN 둘 다로 걸려도 DOI 로 한 번만
        logger.debug("묶음 %.1f초: %d저널, 항목 %d건", time.monotonic() - started, len(batch), len(items))
        for name in sorted(per):
            logger.debug("  %s: %d건", name, len(per[name]))
        return {name: (list(d.values()), None) for name, d in per.items()}

    out: dict = {}
    if not batches:
        return out
    out.update(run(batches[0]))
    limit = cs.stats.get("concurrency_limit")
    workers = max(1, min(requested_workers, limit or 1))  # 한도를 모르면 1
    logger.info("동시 요청 %d (요청 %d, 풀 한도 %s), 묶음 %d개", workers, requested_workers, limit or "알 수 없음", len(batches))
    if len(batches) > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            for result in pool.map(run, batches[1:]):
                out.update(result)
    return out


def run_shadow(args, conn, config: dict, today: dt.date) -> int:
    entries = cs.journal_entries(config, mode="shadow", names=_split_names(args.journals))
    runnable = sorted((e for e in entries if e.get("issn")), key=lambda e: e["journal"])
    for e in entries:
        if not e.get("issn"):
            logger.info("ISSN 이 없어 shadow 에서 제외: %s", e["journal"])
    if not runnable:
        print("대상 저널이 없습니다(mode=shadow, ISSN 있음, --journals 에 해당하는 저널).", file=sys.stderr)
        return 1
    end = args.until or (today - dt.timedelta(days=SHADOW_END_LAG_DAYS))
    start = end - dt.timedelta(days=args.days - 1)
    fetch_from = start - dt.timedelta(days=args.margin_days)
    logger.info("shadow 구간: %s ~ %s (%d일), Crossref 조회 %s ~ %s, 저널 %d곳", start, end, args.days, fetch_from, end, len(runnable))

    fetched = fetch_shadow(runnable, fetch_from, end, args.workers or DEFAULT_WORKERS, args.batch_size)

    results, failed = [], 0
    for entry in runnable:  # runnable 은 저널명 순 - 출력이 완료 순서와 무관하게 결정적이다
        items, err = fetched[entry["journal"]]
        if err is not None:
            logger.error("Crossref 조회 실패 (%s): %s", entry["journal"], err)
            failed += 1
            continue
        names = [entry["journal"]] + cs.alias_names(config, entry["journal"])
        row = shadow_compare(conn, entry["journal"], names, items, start, end)
        row["publisher"] = entry.get("publisher", "")
        row["issn"] = entry["issn"][0]
        results.append(row)

    active = [r for r in results if r["baseline_active"]]
    inactive = [r for r in results if not r["baseline_active"]]
    ordered = sorted(active, key=lambda r: (r["coverage_pct"] is None, r["coverage_pct"] or 0, -r["crossref_count"], r["journal"]))
    print(f"\n=== Crossref 대비 메일 포착률 (shadow, 읽기 전용): {start} ~ {end} ===")
    print(_table(
        ["journal", "pub", "crossref", "matched", "missing", "cov%", "lag_d", "arr_lag_d", "email_only"],
        [[r["journal"], r["publisher"], r["crossref_count"], r["matched_in_db"], r["missing_in_db"],
          _fmt(r["coverage_pct"]), _fmt(r["median_lag_days"]), _fmt(r["median_arrival_lag_days"]), r["email_only"]] for r in ordered],
        left=2,
    ))
    overall = _aggregate(active)
    by_pub = {}
    for r in active:
        by_pub.setdefault(r["publisher"], []).append(r)
    pub_stats = {k: _aggregate(v) for k, v in sorted(by_pub.items())}
    print("\n[출판사별]")
    print(_table(
        ["publisher", "journals", "crossref", "matched", "cov%", "lag_d", "arr_lag_d", "email_only"],
        [[k, v["journals"], v["crossref_count"], v["matched_in_db"], _fmt(v["coverage_pct"]), _fmt(v["median_lag_days"]),
          _fmt(v["median_arrival_lag_days"]), v["email_only"]] for k, v in pub_stats.items()]
        + [["전체", overall["journals"], overall["crossref_count"], overall["matched_in_db"], _fmt(overall["coverage_pct"]),
            _fmt(overall["median_lag_days"]), _fmt(overall["median_arrival_lag_days"]), overall["email_only"]]],
    ))
    if inactive:
        print("\n[메일 기준선 없음 - 최근 180일 DB 행 0건이라 전체 집계에서 제외]")
        for r in sorted(inactive, key=lambda r: (-r["crossref_count"], r["journal"])):
            print(f"  {r['journal']}: crossref {r['crossref_count']}건")
    for r in ordered[:5]:
        if r["missing_samples"]:
            print(f"\n[{r['journal']}] 메일에 없는 Crossref 항목 예시: " + " | ".join(t[:70] for t in r["missing_samples"]))

    record = {
        "ts": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "mode": "shadow", "window": {"from": start.isoformat(), "to": end.isoformat(), "days": args.days, "margin_days": args.margin_days},
        "overall": overall, "by_publisher": pub_stats,
        "journals": [{k: v for k, v in r.items() if not k.startswith("_")} for r in sorted(results, key=lambda r: r["journal"])],
        "failed_journals": failed,
    }
    os.makedirs(os.path.dirname(SHADOW_LOG), exist_ok=True)
    with open(SHADOW_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
    logger.info("로그 추가: %s", SHADOW_LOG)
    return 1 if failed else 0


# ---------- CLI ----------

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ("ingest", "shadow", "-h", "--help"):
        argv.insert(0, "ingest")  # 모드를 생략하면 ingest
    parser = argparse.ArgumentParser(
        description="Crossref 에서 논문을 가져오거나(ingest) 메일 포착률을 잰다(shadow)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="mode", required=True)

    def common(p):
        p.add_argument("--journals", nargs="+", metavar="NAME", help="이 저널만(대소문자 무시, 쉼표 구분도 가능)")
        p.add_argument("--config", default=None, help="crossref_journals.json 경로(기본: 프로젝트 루트)")
        p.add_argument("--debug", action="store_true", help="DEBUG 로그(요청·묶음·저널별 소요 시간)")

    ing = sub.add_parser("ingest", help="mode=ingest 저널의 새 DOI 를 DB 에 넣는다(기본 드라이런)")
    common(ing)
    ing.add_argument("--apply", action="store_true", help="실제로 DB 에 저장하고 커서를 옮긴다 (기본은 드라이런)")
    ing.add_argument("--skip-backup", action="store_true", help="--apply 전에 backup_db 백업을 생략")
    ing.add_argument("--since", type=_date_arg, default=None, metavar="YYYY-MM-DD",
                     help="조회 시작일(created). 주면 저널별 커서보다 우선한다. 기본: 커서, 커서도 없으면 56일 전")
    ing.add_argument("--verbose", action="store_true", help="신규·제외 제목을 전부 출력")
    sh = sub.add_parser("shadow", help="Crossref 대비 메일 포착률 측정(읽기 전용)")
    common(sh)
    sh.add_argument("--days", type=int, default=SHADOW_DEFAULT_DAYS, help="구간 길이(일), 기본 14")
    sh.add_argument("--until", type=_date_arg, default=None, metavar="YYYY-MM-DD",
                    help="구간 끝(포함). 기본: 3일 전. 오래된 DB 사본이면 사본 시점 -3일로 지정")
    sh.add_argument("--margin-days", type=int, default=SHADOW_MARGIN_DAYS,
                    help="구간 앞쪽으로 더 가져올 Crossref 기간(기본 14). 메일이 몇 주~몇 달 늦는 저널(RSC 등)은 늘린다")
    sh.add_argument("--workers", type=int, default=None,
                    help="동시 Crossref 요청 상한(기본 4). 실제 동시 수는 풀의 X-Concurrency-Limit 을 넘지 않는다 "
                         "(public pool 1, CROSSREF_MAILTO 를 쓰는 polite pool 3)")
    sh.add_argument("--batch-size", type=int, default=SHADOW_BATCH, help="한 질의에 묶는 저널 수(기본 30, 1이면 저널마다 질의)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    cs.reset_stats()
    started = time.monotonic()
    config = cs.load_config(args.config)
    today = cs._today_utc()
    write = args.mode == "ingest" and args.apply
    try:
        conn = store.connect() if write else store.connect(readonly=True)  # 쓰기가 필요 없으면 SQLite 가 쓰기를 거부하는 연결
    except FileNotFoundError as e:
        print(f"정본 DB가 없습니다: {e}", file=sys.stderr)
        return 1
    try:
        code = (run_ingest if args.mode == "ingest" else run_shadow)(args, conn, config, today)
    finally:
        conn.close()
    logger.info("Crossref 요청 %d회 (재시도 %d회), 소요 %.0f초", cs.stats["requests"], cs.stats["retries"], time.monotonic() - started)
    return code


if __name__ == "__main__":
    sys.exit(main())
