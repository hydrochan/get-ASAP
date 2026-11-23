"""Crossref REST API 수집원 (라이브러리). CLI는 crossref_fetch.py.

배경:
  일부 출판사는 알림 메일을 잘라서 보낸다(Nature Communications 주 ~200편 중 12편, iScience 63~130편 중 1~2편,
  PCCP 는 주간 메일이 8주 중 4주 누락). Crossref 는 DOI 를 메일보다 먼저(ACS 중앙값 4.2일) 등록하므로,
  이 저널들은 Crossref 에서 직접 채우고 나머지는 같은 방식으로 메일 대비 포착률을 잰다(shadow).

규칙은 모두 결정적이다(Crossref REST API 호출과 JSON 파싱만 있고 AI/ML 은 없다):
  - 제목: title[0] 에서 JATS/HTML 태그를 공백 없이 제거(CO<sub>2</sub> -> CO2) -> html.unescape -> 공백 정리.
  - 발행일: published-online 이 연-월-일을 모두 가지면 그것, 아니면 created(UTC) 날짜.
    미래 날짜는 main._effective_pub_date 와 같은 규칙으로 오늘(UTC)로 줄인다.
  - 비논문 제외: update-to 가 있는 항목(정정·철회 공지), 정정류 제목, Wiley 표지 제목, Nature(10.1038/) 의 뉴스(d41586-)·
    received/accepted 이력 없는 항목, parsers/filters.is_valid_paper_title 이 거절하는 제목.
  - 중복: store.normalize_title 키 정확 일치(전체 DB; 저장 시 유일 인덱스와 같은 기준) + 같은 저널 최근 ~180일 행과의
    공백 무시 키·영숫자 키(alnum_key) 일치(메일 파서가 "CO 2" 로 쪼갠 제목, 하이픈·기호·엔티티 차이 대응).
    접두·유사도 일치는 shadow 측정에만 쓴다(Part I/II 오병합 방지).

main.py 연결은 별도 작업이다. email_skip_journals() 가 연결용 헬퍼다.
"""
import dataclasses
import datetime as dt
import difflib
import html
import json
import logging
import os
import re
import sqlite3
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request

import store
from models import PaperMetadata
from parsers.filters import SKIP_SUBSTRINGS, SKIP_TITLES, has_skip_label, is_valid_paper_title

logger = logging.getLogger(__name__)

API_BASE = "https://api.crossref.org"
USER_AGENT = "get-ASAP (+https://get-asap.hydrochan.com)"
JOURNALS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "crossref_journals.json")

ROWS_PER_PAGE = 1000
MAX_PAGES = 100            # 커서 무한 루프 방지(1000행 x 100쪽)
MAX_RETRIES = 4            # 429/5xx/네트워크 오류 재시도 횟수(첫 시도 제외)
# select 로 쓰는 필드만 받는다(없으면 참고문헌·초록까지 와서 1000행이 1MB 가 된다 - 실측 33KB vs 1.05MB).
# title/DOI/created/published-online = 제목·URL·발행일, type·update-to = 비논문 제외. ISSN 은 여러 저널을 한 번에 조회할 때
# 항목을 저널로 되돌리는 데만 쓴다. assertion = Nature 의 received/accepted(동료심사 여부) 판정용 - 이 필드가 select 에서
# 빠지면 모든 Nature 논문이 nature-not-peer-reviewed 로 버려지므로 테스트가 포함 여부를 고정한다.
SELECT_FIELDS = "DOI,title,type,created,published-online,update-to,assertion"
SELECT_FIELDS_MULTI = SELECT_FIELDS + ",ISSN"
REQUEST_TIMEOUT = 60       # 요청 한 건의 소켓 제한(초)
DEDUP_WINDOW_DAYS = 180    # 공백 무시 키 비교 대상: 같은 저널의 최근 이 기간 행
CURSOR_OVERLAP_DAYS = 2    # 커서 날짜보다 이만큼 앞에서 다시 훑는다(Crossref 색인 지연 대비; 중복은 DB 가 걸러낸다)
MIN_REQUEST_GAP = 0.05
MAX_REQUEST_GAP = 2.0

stats = {"requests": 0, "retries": 0, "concurrency_limit": None}
_stats_lock = threading.Lock()
_sleep = time.sleep  # 테스트에서 교체


def _bump(key: str) -> None:
    with _stats_lock:
        stats[key] += 1


def reset_stats() -> None:
    with _stats_lock:
        stats["requests"] = 0
        stats["retries"] = 0
        stats["concurrency_limit"] = None  # 풀이 허용하는 동시 요청 수(X-Concurrency-Limit). 첫 응답 전에는 모른다


# ---------- HTTP ----------

def _url(path: str, params: dict) -> str:
    """API URL. CROSSREF_MAILTO 환경변수가 있을 때만 mailto 를 붙인다(코드에 이메일을 두지 않는다)."""
    params = dict(params)
    mailto = os.getenv("CROSSREF_MAILTO", "").strip()
    if mailto:
        params["mailto"] = mailto
    return f"{API_BASE}{path}?{urllib.parse.urlencode(params, safe=',:*')}"


def _pace(headers) -> None:
    """X-Rate-Limit-Limit / -Interval 헤더가 있으면 호출 사이를 interval/limit 초만큼 쉰다(느슨하게 지킨다)."""
    gap = MIN_REQUEST_GAP
    try:
        limit = float(headers.get("X-Rate-Limit-Limit"))
        interval = float(str(headers.get("X-Rate-Limit-Interval", "1s")).rstrip("s"))
        if limit > 0:
            gap = max(gap, min(MAX_REQUEST_GAP, interval / limit))
    except (TypeError, ValueError):
        pass
    _sleep(gap)


def _note_concurrency(headers) -> None:
    """X-Concurrency-Limit 헤더(public pool 은 1, polite pool 은 3)를 기록한다 - crossref_fetch 가 동시 요청 수를 맞추는 데 쓴다."""
    try:
        limit = int(headers.get("X-Concurrency-Limit"))
    except (TypeError, ValueError):
        return
    if limit > 0:
        with _stats_lock:
            stats["concurrency_limit"] = limit


def _get_json(url: str) -> dict:
    """GET + JSON 파싱. 429/5xx/네트워크 오류/깨진 JSON 은 지수 백오프로 최대 MAX_RETRIES 회 재시도한다.
    그 밖의 4xx(404 등)는 즉시 예외."""
    delay = 1.0
    for attempt in range(MAX_RETRIES + 1):
        _bump("requests")
        wait = delay
        try:
            req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = json.load(resp)
                headers = resp.headers
            _note_concurrency(headers)
            _pace(headers)
            return body
        except urllib.error.HTTPError as e:
            if e.code != 429 and e.code < 500:
                raise
            retry_after = e.headers.get("Retry-After") if e.headers else None
            try:
                wait = min(60.0, max(delay, float(retry_after))) if retry_after else delay
            except ValueError:
                pass
            err = e
        except (urllib.error.URLError, TimeoutError, ConnectionError, json.JSONDecodeError) as e:
            err = e
        if attempt == MAX_RETRIES:
            raise err
        _bump("retries")
        logger.warning("Crossref 요청 실패(%s), %.1f초 뒤 재시도 (%d/%d)", err, wait, attempt + 1, MAX_RETRIES)
        _sleep(wait)
        delay *= 2
    raise AssertionError("unreachable")


def _fetch_paged(path: str, flt: str, select: str) -> list[dict]:
    """cursor 로 쪽을 끝까지 넘기며 항목을 모은다."""
    items: list[dict] = []
    cursor = "*"
    for _ in range(MAX_PAGES):
        params = {"filter": flt, "rows": ROWS_PER_PAGE, "cursor": cursor, "select": select}
        message = _get_json(_url(path, params))["message"]
        page = message.get("items") or []
        items.extend(page)
        cursor = message.get("next-cursor")
        total = message.get("total-results")
        if not page or not cursor or (isinstance(total, int) and len(items) >= total):  # total 로 끝을 알면 빈 쪽 확인 요청을 아낀다
            return items
    logger.warning("쪽수 상한(%d)에 도달해 중단: %s filter=%s", MAX_PAGES, path, flt)
    return items


def _created_filter(from_created, until_created=None) -> str:
    flt = f"type:journal-article,from-created-date:{from_created}"
    if until_created:
        flt += f",until-created-date:{until_created}"
    return flt


def fetch_works(issn: str, from_created, until_created=None) -> list[dict]:
    """저널(ISSN)의 journal-article 을 created 일자 구간(양끝 포함)으로 모두 가져온다. cursor 로 쪽을 넘긴다."""
    return _fetch_paged(f"/journals/{issn}/works", _created_filter(from_created, until_created), SELECT_FIELDS)


def fetch_works_many(issns: list[str], from_created, until_created=None) -> list[dict]:
    """여러 ISSN 의 journal-article 을 한 번에 가져온다(같은 이름 필터 issn: 를 반복하면 OR). 항목마다 ISSN 목록이 붙어 있어
    저널로 되돌릴 수 있다. public pool 은 동시 요청이 1건이라 요청 수를 줄이는 것이 가장 효과적이다(저널당 1건 -> 30저널당 몇 건)."""
    flt = _created_filter(from_created, until_created) + "".join(f",issn:{i}" for i in issns)
    return _fetch_paged("/works", flt, SELECT_FIELDS_MULTI)


# ---------- 항목 -> 논문 ----------

_TAG_RE = re.compile(r"</?[A-Za-z][^<>]*>")  # "x < 5 and y > 3" 같은 본문 부등호는 건드리지 않도록 태그 모양만
# Crossref(RSC·일부 Elsevier/Wiley)는 <sub>/<sup> 앞뒤에 공백·개행을 넣어 둔다 -> 그대로 두면 "CF 3 -substituted"
# 닫는 태그 뒤 공백은 "-substituted"·"XC<sub>2" 처럼 이어지는 경우만 지운다("CO2 reduction"·"CO2 Reduction"의 띄어쓰기는 보존).
_SS = r"(?:[A-Za-z][\w.-]*:)?su[bp]"  # sub/sup, jats:/mml: 접두어 허용
# 여는 태그 앞 공백은 <sub> 일 때만 지운다("CO <sub>2" -> "CO2"). <sup> 앞 공백은 보존한다("Tracking <sup>13</sup>C" -> "Tracking 13C").
_SCRIPT_OPEN_RE = re.compile(
    rf"(?:\s*(?=<(?:[A-Za-z][\w.-]*:)?sub(?=[\s/>])))?(<{_SS}(?=[\s/>])[^<>]*>)\s*", re.IGNORECASE
)
_SCRIPT_CLOSE_RE = re.compile(
    rf"\s*(</{_SS}\s*>)(?:\s+(?=-\w|[)\],.;:]|(?-i:(?:[A-Z][a-z]?\d*)+)<{_SS}(?=[\s/>])))?", re.IGNORECASE
)
_WS_RE = re.compile(r"\s+")
# 정정·철회 공지 라벨. 키워드만으로는 거르지 않는다 - 뒤에 라벨 구분자(끝/콜론·대시/to·note·notice/따옴표로 인용한 제목)가 와야 한다.
# ("Correction for multiple testing in ..." 같은 연구 제목은 통과. 진짜 공지는 Crossref update-to 가 따로 걸러낸다.)
# 'editorial' 은 여기서 뺐다 - 라벨 달린 편집자 글은 parsers.filters 가, "Editorial and peer review dynamics ..." 같은 연구 제목은 통과시킨다.
_NON_RESEARCH_RE = re.compile(
    r"^(?:(?:author|publisher)\s+)?"
    r"(?:addition/correction|correction|retraction|retracted\s+article|retracted|erratum|corrigendum|expression of concern|addendum)"
    r"(?:$|\s*[:\-‐‑–—―−]|\s+(?:to|note|notice)\b|\s+(?:of|for|on)\s+[“\"'‘])",
    re.IGNORECASE,
)


def clean_title(raw: str) -> str:
    """JATS/HTML 태그를 공백 없이 제거 -> 엔티티 해제 -> 공백 정리. CO<sub>2</sub> -> CO2.

    <sub>/<sup> 안팎의 공백은 먼저 지운다("CF\\n <sub>3</sub>\\n -substituted" -> CF3-substituted).
    <i>/<b> 같은 다른 태그 주변 공백은 그대로 둔다.
    """
    raw = _SCRIPT_CLOSE_RE.sub(r"\1", _SCRIPT_OPEN_RE.sub(r"\1", raw or ""))
    return _WS_RE.sub(" ", html.unescape(_TAG_RE.sub("", raw))).strip()


def item_title(item: dict) -> str:
    titles = item.get("title") or []
    return clean_title(titles[0]) if titles else ""


def _full_date(node) -> str | None:
    """{"date-parts": [[Y, M, D]]} 가 연-월-일을 모두 가진 실제 달력 날짜일 때만 YYYY-MM-DD."""
    try:
        parts = node["date-parts"][0]
        if len(parts) < 3:
            return None
        return dt.date(int(parts[0]), int(parts[1]), int(parts[2])).isoformat()
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def created_datetime(item: dict) -> str | None:
    """Crossref created.date-time (UTC, `YYYY-MM-DDTHH:MM:SSZ`). 없으면 date-parts 로 자정을 만든다. 둘 다 없으면 None."""
    created = item.get("created") or {}
    stamp = created.get("date-time")
    if isinstance(stamp, str) and re.match(r"^\d{4}-\d{2}-\d{2}", stamp):
        return stamp
    day = _full_date(created)
    return f"{day}T00:00:00Z" if day else None


def created_date(item: dict) -> str | None:
    stamp = created_datetime(item)
    return stamp[:10] if stamp else None


def effective_pub_date(paper_date: str, ref_date: str) -> str:
    """main._effective_pub_date 와 같은 규칙(발행일이 기준일보다 미래면 기준일). main 을 import 하면 google 라이브러리·.env 로딩이
    딸려오고 미커밋 작업과 엮이므로 복사했다. 두 구현의 동치는 regression_tests/test_crossref_source.py 가 보증한다."""
    if not paper_date:
        return ref_date
    paper_norm = store.normalize_pub_date(paper_date)
    ref_norm = store.normalize_pub_date(ref_date)
    if paper_norm is None or ref_norm is None:
        return paper_date
    return ref_date if paper_norm > ref_norm else paper_date


def _today_utc() -> dt.date:
    return dt.datetime.now(dt.timezone.utc).date()


def to_paper(item: dict, journal: str, today: dt.date | None = None) -> PaperMetadata | None:
    """Crossref 항목 -> PaperMetadata. DOI 나 제목이 없으면 None."""
    doi = (item.get("DOI") or "").strip()
    title = item_title(item)
    if not doi or not title:
        return None
    pub = _full_date(item.get("published-online")) or created_date(item)
    if pub:
        pub = effective_pub_date(pub, (today or _today_utc()).isoformat())
    return PaperMetadata(
        title=title,
        journal=journal,
        date=pub or "",
        url="https://doi.org/" + urllib.parse.quote(doi, safe="/()-._~:;"),
    )


# Wiley 표지 항목: 제목 끝에 "(Adv. Mater. 55/2026)" 같은 "<저널 약칭> <호>/<연도>" 괄호가 붙는다(2026-08~11 Wiley 8,174건 중 339건,
# 약칭 7종 전부 표지). 실제 연구 제목은 이 모양으로 끝나지 않는다(DB 제목 0건·Nature Crossref 4,801건 0건 일치).
# 호 번호 뒤 슬래시가 둘인 표지("Adv. Mater. 28//2026")도 있다(2026-11 백필 리허설에서 발견).
_COVER_TITLE_RE = re.compile(r"\(\s*[A-Z][\w.&\- ]{1,40}?\s\d{1,3}/{1,2}(?:19|20)\d{2}\s*\)\s*$")
_NATURE_DOI_PREFIX = "10.1038/"
# 편집자 글·행사 공지·논평 라벨 (2026-11-23 유출 실측: Crossref 가 journal-article 로 등록하지만 연구 논문이 아닌 항목).
# 패턴은 제목 앞머리(^)에 고정하거나 DOI 접두로 출판사를 한정하고, 운영 DB 정본 행 전체(메일 행 포함) 제목과 대조해
# 연구 논문 오탐이 0건인 것만 남겼다(regression_tests/test_crossref_source.py 가 양·음 사례를 고정한다).
_QUOTE = "\"'“‘"
_EDITORIAL_ANY_RE = re.compile(  # 출판사 무관
    rf"^(?:addition\s+(?:and\s+correction\s+)?(?:to|for)\s+[{_QUOTE}]"  # ACS 정정: Addition and Correction to “...”
    r"|editor['’]?s?\s+notes?\s+to\s+(?:the\s+)?article\b"              # iScience: Editor Note to article entitled “...”
    r"|comments?\s+on\s*:"                                              # Small: Comment on: ... (콜론 변형; reply to: 등은 filters 가 이미 거른다)
    r"|commentary\s*:"
    r"|snapshot\s*:)",                                                  # Cell Press SnapShot:
    re.IGNORECASE,
)
_ACS_DOI_PREFIX = "10.1021/"
_ACS_EDITORIAL_RE = re.compile(  # ACS 편집자 글: 행사·추모·인터뷰·저널 소식
    r"^(?:celebrating|announcing|(?:a\s+)?tribute\s+to|interview\s+with|a\s+conversation\s+with"
    r"|editor['’]s\s+(?:highlights|choice)|\d+\s+years?\s+of\b|introducing\s+the\b.*\b(?:collection|series|special\s+issue)\b)"
    r"|\bspecial\s+issue\b|\bnobel\s+symposium\b|\bin\s+memory\s+of\b|\bES&T\b|\bappoints\b"
    r"|\b(?:ACS\s+(?:Nano|Catalysis|Omega|Energy\s+Letters|Photonics|Central\s+Science|Sustainable|Applied|Materials|Physical|Polymers"
    r"|Measurement|Engineering|Chemical\s+Biology|Organic|Nanoscience|Au)|JACS\s*Au)\b",  # 제 저널 이름이 제목에 나오는 글
    re.IGNORECASE,
)
_CELL_DOI_PREFIXES = ("10.1016/j.chempr.", "10.1016/j.checat.", "10.1016/j.joule.", "10.1016/j.xcrp.", "10.1016/j.cell.",
                      "10.1016/j.isci.", "10.1016/j.matt.")
_CELL_EDITORIAL_RE = re.compile(r"^(?:chem\s+at\s+10|reaction|catalyst|voices)\s*:", re.IGNORECASE)  # Chem 10주년·Reaction·Catalyst 논평
# RSC: 편집·서문·테마호 안내는 논문 번호 첫 자리가 9(d6cp90114j, d6sc90197b). 운영 DB 9로 시작하는 24건 전부 비논문, 연구 논문은 0/1.
_RSC_EDITORIAL_DOI_RE = re.compile(r"^10\.1039/d\d[a-z]{2,3}9\d{4}[a-z]$")


def _editorial_title(doi: str, title: str) -> bool:
    """편집자 글·행사 공지·논평 라벨(출판사별 규칙). 출판사 한정 규칙은 DOI 접두로 범위를 좁힌다."""
    doi = (doi or "").strip().lower()
    title = title or ""
    if _EDITORIAL_ANY_RE.match(title):
        return True
    if doi.startswith(_ACS_DOI_PREFIX) and _ACS_EDITORIAL_RE.search(title):
        return True
    if doi.startswith(_CELL_DOI_PREFIXES) and _CELL_EDITORIAL_RE.match(title):
        return True
    return _RSC_EDITORIAL_DOI_RE.match(doi) is not None


_SCIENCE_DOI_PREFIX = "10.1126/science."  # Science 본지만(Science Advances 10.1126/sciadv., Science Signaling 10.1126/scisignal. 은 해당 없음)


def _is_nature(item: dict) -> bool:
    return (item.get("DOI") or "").strip().lower().startswith(_NATURE_DOI_PREFIX)


def _is_science(item: dict) -> bool:
    return (item.get("DOI") or "").strip().lower().startswith(_SCIENCE_DOI_PREFIX)


def _has_peer_review_dates(item: dict) -> bool:
    """Crossref assertion 에 received/accepted 가 있으면 동료심사를 거친 논문이다(Nature 는 뉴스·해설에 이 이력을 달지 않는다)."""
    return any(isinstance(a, dict) and a.get("name") in ("received", "accepted") for a in item.get("assertion") or [])


def skip_reason(item: dict, title: str) -> str | None:
    """연구 논문이 아니면 제외 사유 라벨, 연구 논문이면 None.

    update-to(정정·철회 공지) / type(journal-article 아님) / correction-title(정정류 제목) / cover-title(Wiley 표지) / editorial-title(편집자 글·행사 공지·논평 라벨, 출판사별) /
    nature-magazine(10.1038/d41586-, Nature 뉴스·해설) / nature-not-peer-reviewed(10.1038/ 인데 received·accepted 이력 없음) /
    science-not-peer-reviewed(10.1126/science. 인데 received·accepted 이력 없음 - Science 본지 뉴스·사설·서평) /
    short-title(parsers.filters 의 4단어 미만 규칙에만 걸림 - 짧은 실제 논문 제목이 섞일 수 있어 따로 센다) /
    filters(그 밖의 parsers.filters 규칙: 표지·목차·저널명 등).
    """
    if item.get("update-to"):
        return "update-to"
    if item.get("type") not in (None, "journal-article"):
        return "type"
    if _NON_RESEARCH_RE.match(title or ""):
        return "correction-title"
    if _COVER_TITLE_RE.search(title or ""):
        return "cover-title"
    if _editorial_title(item.get("DOI") or "", title):
        return "editorial-title"
    nature = _is_nature(item)
    if nature:
        if item["DOI"].strip().lower().startswith("10.1038/d41586-"):
            return "nature-magazine"
        if not _has_peer_review_dates(item):  # 키가 아예 없는 항목도 전부 비논문이었다 - 예외를 두지 않는다
            return "nature-not-peer-reviewed"
    science = _is_science(item)
    if science and not _has_peer_review_dates(item):
        # Science 본지는 뉴스·사설·서평·정책 글도 journal-article 로 등록한다(2026-11 백필 리허설: 신규 672건 중 대부분).
        # 연구 논문은 메일에 잡힌 192건 전부 received/accepted 이력이 있고 비논문에는 거의 없다.
        return "science-not-peer-reviewed"
    if is_valid_paper_title(title):
        return None
    low = (title or "").strip().casefold()
    only_short = (
        len(low.split()) < 4 and len(low) >= 10 and low not in SKIP_TITLES
        and not has_skip_label(low) and not any(s in low for s in SKIP_SUBSTRINGS)
    )
    if only_short:
        return None if (nature or science) else "short-title"  # 동료심사 확인된 Nature·Science 논문은 짧은 제목("Multilevel molecular weaving")이어도 통과
    return "filters"


def is_research_item(item: dict, title: str) -> bool:
    """연구 논문이 아닌 항목(정정·철회·편집 글 등)을 거른다."""
    return skip_reason(item, title) is None


# ---------- 중복 판정 ----------

def _squash(key: str) -> str:
    return _WS_RE.sub("", key)


ALNUM_MIN_LEN = 10   # 이보다 짧은 영숫자 키는 우연히 겹치기 쉬워 쓰지 않는다
PREFIX_MIN_LEN = 25  # shadow 접두 일치: 짧은 쪽이 이 길이 이상일 때만
FUZZY_RATIO = 0.9    # shadow 유사도 일치 하한


def alnum_key(title: str) -> str:
    """영숫자만 남긴 비교 키(DB title_key 와 별개의 메모리 전용 키). HTML 엔티티를 두 번 풀고(Crossref 에 "&gt;" 가 그대로 있다)
    NFKC·casefold 한 뒤 글자·숫자만 남긴다 -> 하이픈·공백 차이, 결합 문자·기호, 끝의 " †"(RSC), 사설 영역 문자(Crossref 가 "≡" 를
    바꿔 넣는다)가 모두 사라진다. ALNUM_MIN_LEN 미만이면 빈 문자열."""
    text = unicodedata.normalize("NFKC", html.unescape(html.unescape(title or ""))).casefold()
    key = "".join(ch for ch in text if ch.isalnum())
    return key if len(key) >= ALNUM_MIN_LEN else ""


def loose_match(a: str, b: str) -> bool:
    """두 영숫자 키가 같은 논문으로 보이는가: 짧은 쪽(>= PREFIX_MIN_LEN)이 긴 쪽의 접두이거나 유사도 >= FUZZY_RATIO.
    오탐 가능성이 있어("Part I"/"Part II") shadow 측정에만 쓰고 수집 중복 판정에는 쓰지 않는다."""
    if not a or not b:
        return False
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    if len(short) >= PREFIX_MIN_LEN and long_.startswith(short):
        return True
    if len(short) < FUZZY_RATIO * len(long_):
        return False
    m = difflib.SequenceMatcher(None, short, long_, autojunk=False)
    return m.real_quick_ratio() >= FUZZY_RATIO and m.quick_ratio() >= FUZZY_RATIO and m.ratio() >= FUZZY_RATIO


class TitleIndex:
    """제목 키 조회표. exact 는 normalize_title 키, nospace 는 거기서 공백을 모두 뺀 키, alnum 은 alnum_key(영숫자만).
    값은 그 행의 created_at. alnum 은 nospace 와 같은 범위에 들어간다(메모리 전용 - DB 열·인덱스는 그대로)."""

    def __init__(self):
        self.exact: dict[str, str] = {}
        self.nospace: dict[str, str] = {}
        self.alnum: dict[str, str] = {}

    def add_key(self, title_key: str, created_at: str = "", *, exact: bool = True, nospace: bool = True,
                title: str | None = None) -> None:
        """title 은 DB 의 원 제목(엔티티·사설 영역 문자 처리용). 없으면 title_key 로 alnum 키를 만든다."""
        if exact:
            self.exact.setdefault(title_key, created_at)
        if nospace:
            self.nospace.setdefault(_squash(title_key), created_at)
            a = alnum_key(title if title is not None else title_key)
            if a:
                self.alnum.setdefault(a, created_at)

    def add_title(self, title: str, created_at: str = "") -> None:
        self.add_key(store.normalize_title(title), created_at, title=title)

    def lookup_key(self, title_key: str, title: str | None = None) -> str | None:
        """이미 정규화된 키(DB title_key)로 조회. 일치하는 행의 created_at, 없으면 None. title 을 주면 alnum 키를 그것으로 만든다."""
        hit = self.exact.get(title_key)
        if hit is None:
            hit = self.nospace.get(_squash(title_key))
        if hit is None:
            a = alnum_key(title if title is not None else title_key)
            hit = self.alnum.get(a) if a else None
        return hit

    def lookup(self, title: str) -> str | None:
        """제목으로 조회. 일치하는 행의 created_at, 없으면 None."""
        return self.lookup_key(store.normalize_title(title), title)

    def lookup_loose(self, title: str) -> str | None:
        """접두·유사도 일치(loose_match). shadow 측정 전용 - 수집 중복 판정에 쓰지 말 것. 일치하는 행의 created_at, 없으면 None."""
        a = alnum_key(title)
        if not a:
            return None
        for key, created_at in self.alnum.items():
            if loose_match(a, key):
                return created_at
        return None


def load_index(
    conn: sqlite3.Connection,
    journals: list[str],
    *,
    global_exact: bool,
    since_created: str | None,
) -> TitleIndex:
    """DB 의 제목 키 조회표(읽기 전용 SELECT).

    global_exact=True 면 exact 키는 전체 DB(tombstone·dup 포함 - 저장 시 유일 인덱스와 같은 기준), False 면 journals 의 행만.
    nospace 키는 항상 journals 의 행만이며 since_created(`YYYY-MM-DD`, created_at 하한)가 있으면 그 이후 행만 쓴다.
    """
    index = TitleIndex()
    marks = ",".join("?" * len(journals))
    if global_exact:
        for r in conn.execute("SELECT title_key, created_at FROM papers ORDER BY created_at"):
            index.add_key(r[0], r[1], nospace=False)
    else:
        for r in conn.execute(f"SELECT title_key, created_at FROM papers WHERE journal IN ({marks}) ORDER BY created_at", journals):
            index.add_key(r[0], r[1], nospace=False)
    sql = f"SELECT title_key, created_at, title FROM papers WHERE journal IN ({marks})"
    args: list = list(journals)
    if since_created:
        sql += " AND created_at >= ?"
        args.append(since_created)
    for r in conn.execute(sql + " ORDER BY created_at", args):
        index.add_key(r[0], r[1], exact=False, title=r[2])
    return index


@dataclasses.dataclass
class Partition:
    """fetch 결과 분류. new 만 저장 대상."""
    new: list[PaperMetadata] = dataclasses.field(default_factory=list)
    non_research: list[tuple[str, str]] = dataclasses.field(default_factory=list)  # (skip_reason 라벨, 제목)
    in_db: list[str] = dataclasses.field(default_factory=list)
    batch_dup: list[str] = dataclasses.field(default_factory=list)
    fetched: int = 0
    max_created: str | None = None  # 훑은 모든 항목(건너뛴 것 포함)의 created 최댓값 -> 커서


def partition_items(items: list[dict], journal: str, index: TitleIndex, today: dt.date | None = None) -> Partition:
    """항목을 비논문 / DB 에 이미 있음 / 이번 배치 안 중복 / 신규로 나눈다."""
    out = Partition(fetched=len(items))
    seen = TitleIndex()
    for item in sorted(items, key=lambda i: created_datetime(i) or ""):
        stamp = created_datetime(item)
        if stamp and (out.max_created is None or stamp > out.max_created):
            out.max_created = stamp
        title = item_title(item)
        paper = to_paper(item, journal, today)
        reason = "no-doi-or-title" if paper is None else skip_reason(item, title)
        if reason:
            out.non_research.append((reason, title or str(item.get("DOI"))))
        elif index.lookup(title) is not None:
            out.in_db.append(title)
        elif seen.lookup(title) is not None:
            out.batch_dup.append(title)
        else:
            seen.add_title(title)
            out.new.append(paper)
    return out


# ---------- 설정 / 커서 ----------

def load_config(path: str | None = None) -> dict:
    with open(path or JOURNALS_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def journal_entries(config: dict, *, mode: str | None = None, names: list[str] | None = None) -> list[dict]:
    """설정의 저널 항목. mode 로 거르고, names 가 있으면 이름(대소문자 무시)으로 거른다. alias_of 항목은 제외한다(조회하지 않는다)."""
    wanted = {n.strip().casefold() for n in names} if names else None
    return [
        e for e in config.get("journals", [])
        if not e.get("alias_of")
        and (mode is None or e.get("mode") == mode)
        and (wanted is None or e["journal"].casefold() in wanted)
    ]


def alias_names(config: dict, journal: str) -> list[str]:
    """journal 을 가리키는 별칭 이름들(DB 행 매칭에 이름을 보탠다)."""
    return [e["journal"] for e in config.get("journals", []) if e.get("alias_of") == journal]


def email_skip_journals(path: str | None = None) -> set[str]:
    """메일 파싱을 건너뛸 저널 이름 집합(= mode 가 "ingest" 인 저널). skip_email_for_ingest 가 false 면 빈 집합.
    main.py 연결용 헬퍼다."""
    config = load_config(path)
    if not config.get("skip_email_for_ingest"):
        return set()
    return {e["journal"] for e in config.get("journals", []) if e.get("mode") == "ingest"}


def cursor_key(issn: str) -> str:
    return f"crossref_cursor:{issn}"


def get_cursor(conn: sqlite3.Connection, issn: str) -> str | None:
    """저널별 커서(마지막으로 훑은 created 시각, UTC). 없으면 None."""
    row = conn.execute("SELECT value FROM meta WHERE key=?", (cursor_key(issn),)).fetchone()
    return row[0] if row else None


def set_cursor(conn: sqlite3.Connection, issn: str, created: str) -> None:
    """커서를 앞으로만 옮긴다(csv_seq 메모와 같은 방식: 더 큰 값일 때만 갱신). meta 테이블은 기존 스키마 그대로 쓴다."""
    with store.write_tx(conn):
        conn.execute(
            "INSERT INTO meta(key, value) VALUES(?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value WHERE excluded.value > meta.value",
            (cursor_key(issn), created),
        )


def cursor_from_date(cursor: str) -> str:
    """커서 시각 -> 다음 조회의 from-created-date (겹침 CURSOR_OVERLAP_DAYS 일 포함)."""
    day = dt.date.fromisoformat(cursor[:10]) - dt.timedelta(days=CURSOR_OVERLAP_DAYS)
    return day.isoformat()
