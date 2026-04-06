# Paper Analytics Dashboard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Notion에 축적된 논문 데이터를 Streamlit 대시보드로 시각화하여 키워드 트렌드, 저널별 주제 분포, AI 관심 패턴을 분석한다.

**Architecture:** Notion API에서 월별 DB를 fetch하여 pandas DataFrame으로 변환 + 로컬 CSV 캐싱. scikit-learn TF-IDF로 Title 키워드 추출. Streamlit 4탭 대시보드로 시각화. 리포트 마크다운 export 지원.

**Tech Stack:** Python 3.11+, streamlit, pandas, scikit-learn, matplotlib, seaborn, wordcloud, 기존 notion-client 3.0.0

---

## File Structure

```
get-ASAP/
├── analytics/
│   ├── __init__.py              # 패키지 init (빈 파일)
│   ├── notion_fetcher.py        # Notion → DataFrame 변환 + CSV 캐싱
│   ├── preprocessor.py          # Title 전처리 + TF-IDF 키워드 추출
│   ├── analyzer.py              # 4가지 분석 로직 (트렌드, 크로스탭, 관심, 빈도)
│   └── report.py                # 마크다운 리포트 생성
├── dashboard/
│   └── app.py                   # Streamlit 대시보드 (진입점)
├── tests/
│   ├── test_notion_fetcher.py   # fetcher 단위 테스트
│   ├── test_preprocessor.py     # 전처리/TF-IDF 단위 테스트
│   ├── test_analyzer.py         # 분석 로직 단위 테스트
│   └── test_report.py           # 리포트 생성 단위 테스트
├── cache/                       # CSV 캐시 (gitignored)
└── reports/                     # 생성된 리포트 (gitignored)
```

**Modify:**
- `.gitignore` — `cache/`, `reports/` 추가
- `requirements.txt` — 새 의존성 추가

---

### Task 1: 프로젝트 설정 (의존성 + gitignore)

**Files:**
- Modify: `requirements.txt`
- Modify: `.gitignore`
- Create: `analytics/__init__.py`
- Create: `cache/.gitkeep`
- Create: `reports/.gitkeep`

- [ ] **Step 1: requirements.txt에 새 의존성 추가**

`requirements.txt` 파일 끝에 추가:
```
streamlit>=1.30.0
pandas>=2.0.0
scikit-learn>=1.3.0
matplotlib>=3.7.0
seaborn>=0.13.0
wordcloud>=1.9.0
```

- [ ] **Step 2: .gitignore에 cache/, reports/ 추가**

`.gitignore` 파일 끝에 추가:
```

# Analytics
cache/
reports/
```

- [ ] **Step 3: 디렉토리 및 빈 파일 생성**

```bash
mkdir -p analytics dashboard cache reports tests
touch analytics/__init__.py cache/.gitkeep reports/.gitkeep
```

- [ ] **Step 4: 의존성 설치**

```bash
pip install streamlit pandas scikit-learn matplotlib seaborn wordcloud
```

- [ ] **Step 5: 커밋**

```bash
git add requirements.txt .gitignore analytics/__init__.py cache/.gitkeep reports/.gitkeep
git commit -m "chore: analytics 대시보드 의존성 및 디렉토리 구조 추가"
```

---

### Task 2: Notion Fetcher — Notion DB에서 논문 데이터 fetch + CSV 캐싱

**Files:**
- Create: `analytics/notion_fetcher.py`
- Create: `tests/test_notion_fetcher.py`

- [ ] **Step 1: failing test 작성**

`tests/test_notion_fetcher.py`:
```python
"""notion_fetcher 단위 테스트 — Notion API는 mock 처리"""
import os
import pandas as pd
import pytest
from unittest.mock import patch, MagicMock


def _make_page(title, journal, date, url, status):
    """Notion API 응답 형태의 가짜 page 생성"""
    return {
        "properties": {
            "Title": {"title": [{"plain_text": title}]},
            "Journal": {"select": {"name": journal} if journal else None},
            "Date": {"date": {"start": date} if date else None},
            "URL": {"url": url if url else None},
            "Status": {"select": {"name": status} if status else None},
        }
    }


class TestParsePages:
    """_parse_pages: Notion API 응답 → DataFrame 변환 테스트"""

    def test_basic_conversion(self):
        from analytics.notion_fetcher import _parse_pages

        pages = [
            _make_page(
                "Oxygen Evolution on Perovskite",
                "ACS Catalysis",
                "2026-03-15",
                "https://doi.org/10.1021/acscatal.123",
                "대기중",
            ),
            _make_page(
                "Carbon Dioxide Reduction Review",
                "Nature Catalysis",
                "2026-03-16",
                "https://doi.org/10.1038/s41929-123",
                "관심",
            ),
        ]
        df = _parse_pages(pages)

        assert len(df) == 2
        assert list(df.columns) == ["title", "journal", "date", "url", "status"]
        assert df.iloc[0]["title"] == "Oxygen Evolution on Perovskite"
        assert df.iloc[0]["journal"] == "ACS Catalysis"
        assert df.iloc[1]["status"] == "관심"
        assert pd.api.types.is_datetime64_any_dtype(df["date"])

    def test_missing_optional_fields(self):
        from analytics.notion_fetcher import _parse_pages

        pages = [_make_page("Title Only Paper", "", "", "", "")]
        df = _parse_pages(pages)

        assert len(df) == 1
        assert df.iloc[0]["title"] == "Title Only Paper"
        assert df.iloc[0]["journal"] == ""

    def test_empty_pages(self):
        from analytics.notion_fetcher import _parse_pages

        df = _parse_pages([])
        assert len(df) == 0
        assert list(df.columns) == ["title", "journal", "date", "url", "status"]


class TestFindMonthlyDbs:
    """find_monthly_dbs: 기간 내 월별 DB ID 목록 반환 테스트"""

    @patch("analytics.notion_fetcher._find_monthly_db")
    def test_range_lookup(self, mock_find):
        from analytics.notion_fetcher import find_monthly_dbs

        # 2026-01 ~ 2026-03 중 01, 03만 존재
        mock_find.side_effect = lambda pid, m: (
            f"db-{m}" if m in ("2026-01", "2026-03") else None
        )
        result = find_monthly_dbs("parent-id", "2026-01", "2026-03")

        assert result == {"2026-01": "db-2026-01", "2026-03": "db-2026-03"}
        assert mock_find.call_count == 3


class TestCaching:
    """CSV 캐싱 동작 테스트"""

    def test_save_and_load_cache(self, tmp_path):
        from analytics.notion_fetcher import _save_cache, _load_cache

        df = pd.DataFrame({
            "title": ["Test Paper"],
            "journal": ["Nature"],
            "date": pd.to_datetime(["2026-03-15"]),
            "url": ["https://example.com"],
            "status": ["대기중"],
        })
        _save_cache(df, "2026-03", cache_dir=str(tmp_path))
        loaded = _load_cache("2026-03", cache_dir=str(tmp_path))

        assert loaded is not None
        assert len(loaded) == 1
        assert loaded.iloc[0]["title"] == "Test Paper"

    def test_load_missing_cache(self, tmp_path):
        from analytics.notion_fetcher import _load_cache

        result = _load_cache("2099-01", cache_dir=str(tmp_path))
        assert result is None
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_notion_fetcher.py -v
```
Expected: FAIL — `analytics.notion_fetcher` 모듈 없음

- [ ] **Step 3: notion_fetcher.py 구현**

`analytics/notion_fetcher.py`:
```python
"""Notion 월별 DB에서 논문 데이터를 fetch하여 pandas DataFrame으로 변환.

기존 notion_client_mod.py의 _find_monthly_db()를 재활용.
CSV 캐싱으로 반복 API 호출 방지.
"""
import logging
import os
from datetime import date

import pandas as pd

import config
from notion_auth import get_notion_client
from notion_client_mod import _find_monthly_db, _get_data_source_id

logger = logging.getLogger(__name__)

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")


def _parse_pages(pages: list[dict]) -> pd.DataFrame:
    """Notion API page 목록 → DataFrame 변환"""
    records = []
    for page in pages:
        props = page.get("properties", {})

        # Title
        title_arr = props.get("Title", {}).get("title", [])
        title = title_arr[0].get("plain_text", "") if title_arr else ""

        # Journal
        journal_sel = props.get("Journal", {}).get("select")
        journal = journal_sel.get("name", "") if journal_sel else ""

        # Date
        date_obj = props.get("Date", {}).get("date")
        date_str = date_obj.get("start", "") if date_obj else ""

        # URL
        url = props.get("URL", {}).get("url", "") or ""

        # Status
        status_sel = props.get("Status", {}).get("select")
        status = status_sel.get("name", "") if status_sel else ""

        records.append({
            "title": title,
            "journal": journal,
            "date": date_str,
            "url": url,
            "status": status,
        })

    df = pd.DataFrame(records, columns=["title", "journal", "date", "url", "status"])
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        df["date"] = pd.to_datetime(df["date"])
    return df


def _generate_months(start: str, end: str) -> list[str]:
    """'YYYY-MM' 범위의 월 목록 생성 (inclusive)"""
    from datetime import datetime

    s = datetime.strptime(start, "%Y-%m")
    e = datetime.strptime(end, "%Y-%m")
    months = []
    current = s
    while current <= e:
        months.append(current.strftime("%Y-%m"))
        # 다음 달로 이동
        if current.month == 12:
            current = current.replace(year=current.year + 1, month=1)
        else:
            current = current.replace(month=current.month + 1)
    return months


def find_monthly_dbs(parent_page_id: str, start: str, end: str) -> dict[str, str]:
    """기간 내 존재하는 월별 DB의 {month: db_id} 딕셔너리 반환"""
    months = _generate_months(start, end)
    result = {}
    for month in months:
        db_id = _find_monthly_db(parent_page_id, month)
        if db_id:
            result[month] = db_id
    return result


def _fetch_all_pages(database_id: str) -> list[dict]:
    """DB의 모든 페이지를 pagination하며 fetch"""
    client = get_notion_client()
    data_source_id = _get_data_source_id(database_id)

    all_pages = []
    cursor = None
    while True:
        kwargs = {"page_size": 100}
        if cursor:
            kwargs["start_cursor"] = cursor
        result = client.data_sources.query(data_source_id, **kwargs)
        all_pages.extend(result.get("results", []))
        if not result.get("has_more"):
            break
        cursor = result.get("next_cursor")
    return all_pages


def _save_cache(df: pd.DataFrame, month: str, cache_dir: str = None) -> None:
    """DataFrame을 CSV 캐시로 저장"""
    cache_dir = cache_dir or CACHE_DIR
    os.makedirs(cache_dir, exist_ok=True)
    path = os.path.join(cache_dir, f"papers_{month}.csv")
    df.to_csv(path, index=False)
    logger.info("캐시 저장: %s (%d건)", path, len(df))


def _load_cache(month: str, cache_dir: str = None) -> pd.DataFrame | None:
    """CSV 캐시에서 DataFrame 로드. 없으면 None."""
    cache_dir = cache_dir or CACHE_DIR
    path = os.path.join(cache_dir, f"papers_{month}.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    logger.info("캐시 로드: %s (%d건)", path, len(df))
    return df


def fetch_papers(
    start: str, end: str, force_refresh: bool = False
) -> pd.DataFrame:
    """기간 내 논문 데이터를 fetch (캐시 우선, 현재 월은 항상 re-fetch).

    Args:
        start: 시작 월 (YYYY-MM)
        end: 종료 월 (YYYY-MM)
        force_refresh: True이면 캐시 무시하고 전부 re-fetch

    Returns:
        전체 기간 논문 DataFrame
    """
    parent_page_id = config.NOTION_PARENT_PAGE_ID
    if not parent_page_id:
        raise ValueError("NOTION_PARENT_PAGE_ID 환경변수가 필요합니다.")

    current_month = date.today().strftime("%Y-%m")
    db_map = find_monthly_dbs(parent_page_id, start, end)

    if not db_map:
        logger.warning("기간 %s ~ %s에 해당하는 DB가 없습니다.", start, end)
        return pd.DataFrame(columns=["title", "journal", "date", "url", "status"])

    frames = []
    for month, db_id in db_map.items():
        # 현재 월이거나 강제 갱신이면 API fetch
        if month == current_month or force_refresh:
            logger.info("API fetch: get-ASAP %s", month)
            pages = _fetch_all_pages(db_id)
            df = _parse_pages(pages)
            _save_cache(df, month)
        else:
            df = _load_cache(month)
            if df is None:
                logger.info("캐시 없음, API fetch: get-ASAP %s", month)
                pages = _fetch_all_pages(db_id)
                df = _parse_pages(pages)
                _save_cache(df, month)
        frames.append(df)

    return pd.concat(frames, ignore_index=True)
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
pytest tests/test_notion_fetcher.py -v
```
Expected: 7 passed

- [ ] **Step 5: 커밋**

```bash
git add analytics/notion_fetcher.py tests/test_notion_fetcher.py
git commit -m "feat: Notion → DataFrame fetcher + CSV 캐싱 구현"
```

---

### Task 3: Preprocessor — Title 전처리 + TF-IDF 키워드 추출

**Files:**
- Create: `analytics/preprocessor.py`
- Create: `tests/test_preprocessor.py`

- [ ] **Step 1: failing test 작성**

`tests/test_preprocessor.py`:
```python
"""preprocessor 단위 테스트 — TF-IDF 키워드 추출"""
import pandas as pd
import pytest


class TestCleanTitle:
    """제목 전처리 테스트"""

    def test_lowercase_and_strip(self):
        from analytics.preprocessor import clean_title

        assert clean_title("  Oxygen Evolution Reaction  ") == "oxygen evolution reaction"

    def test_remove_special_chars(self):
        from analytics.preprocessor import clean_title

        assert clean_title("TiO₂-based (photo)catalysis: a review") == "tio2based photocatalysis a review"

    def test_empty_string(self):
        from analytics.preprocessor import clean_title

        assert clean_title("") == ""


class TestRemoveStopwords:
    """학술 불용어 제거 테스트"""

    def test_removes_academic_stopwords(self):
        from analytics.preprocessor import remove_stopwords

        result = remove_stopwords("a novel study of high performance catalysts using advanced methods")
        # "novel", "study", "high", "performance", "using", "advanced" 등은 불용어
        assert "novel" not in result
        assert "study" not in result
        assert "catalysts" in result

    def test_preserves_meaningful_words(self):
        from analytics.preprocessor import remove_stopwords

        result = remove_stopwords("oxygen evolution reaction perovskite electrode")
        assert "oxygen" in result
        assert "perovskite" in result


class TestExtractKeywords:
    """TF-IDF 키워드 추출 테스트"""

    def test_basic_extraction(self):
        from analytics.preprocessor import extract_keywords

        titles = [
            "oxygen evolution reaction on perovskite",
            "oxygen evolution catalyst design",
            "carbon dioxide reduction mechanism",
            "carbon dioxide electrochemical conversion",
            "perovskite solar cell efficiency",
        ]
        df = pd.DataFrame({"title": titles})
        keywords = extract_keywords(df, top_n=5)

        # keywords는 [(keyword, score), ...] 형태
        assert len(keywords) <= 5
        assert all(isinstance(kw, tuple) and len(kw) == 2 for kw in keywords)
        keyword_names = [kw[0] for kw in keywords]
        # "oxygen evolution"이 bigram으로 잡혀야 함
        assert any("oxygen" in kw for kw in keyword_names)

    def test_empty_dataframe(self):
        from analytics.preprocessor import extract_keywords

        df = pd.DataFrame({"title": []})
        keywords = extract_keywords(df, top_n=10)
        assert keywords == []

    def test_top_n_limit(self):
        from analytics.preprocessor import extract_keywords

        titles = [f"keyword{i} paper title" for i in range(20)]
        df = pd.DataFrame({"title": titles})
        keywords = extract_keywords(df, top_n=3)
        assert len(keywords) <= 3


class TestMonthlyKeywords:
    """월별 키워드 추출 테스트"""

    def test_monthly_grouping(self):
        from analytics.preprocessor import extract_keywords_by_month

        df = pd.DataFrame({
            "title": [
                "oxygen evolution catalyst",
                "oxygen reduction reaction",
                "carbon capture method",
                "carbon dioxide conversion",
            ],
            "date": pd.to_datetime([
                "2026-01-15", "2026-01-20",
                "2026-02-10", "2026-02-15",
            ]),
        })
        result = extract_keywords_by_month(df, top_n=3)

        # result는 {month_str: [(keyword, score), ...]} 형태
        assert "2026-01" in result
        assert "2026-02" in result
        assert len(result) == 2
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_preprocessor.py -v
```
Expected: FAIL — `analytics.preprocessor` 모듈 없음

- [ ] **Step 3: preprocessor.py 구현**

`analytics/preprocessor.py`:
```python
"""Title 전처리 및 TF-IDF 키워드 추출.

논문 제목에서 의미 있는 키워드를 추출하고 빈도/중요도를 분석한다.
"""
import re

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

# 영어 기본 불용어 + 학술 논문 공통어
ACADEMIC_STOPWORDS = {
    # 일반 영어 불용어
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
    "being", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "must",
    "it", "its", "this", "that", "these", "those", "as", "not", "no",
    "than", "into", "between", "through", "during", "before", "after",
    "above", "below", "up", "down", "out", "off", "over", "under",
    "about", "each", "every", "both", "all", "any", "few", "more",
    "most", "other", "some", "such", "only", "own", "same", "so",
    "very", "too", "also", "just", "how", "what", "which", "who",
    "when", "where", "why", "here", "there", "then", "once",
    # 학술 논문 공통어 (의미 약함)
    "novel", "new", "study", "studies", "effect", "effects", "based",
    "using", "via", "toward", "towards", "recent", "highly",
    "high", "performance", "enhanced", "improved", "efficient",
    "advanced", "superior", "excellent", "outstanding", "remarkable",
    "achieving", "enabling", "driven", "induced", "mediated",
    "facile", "simple", "rapid", "one", "two", "three",
    "strategy", "approach", "method", "insight", "insights",
    "role", "review", "perspective", "progress", "challenges",
    "opportunities", "future", "overview", "comprehensive",
    "first", "report", "reveals", "revealed", "revealing",
}


def clean_title(title: str) -> str:
    """제목 전처리: 소문자화, 특수문자 제거"""
    if not title:
        return ""
    text = title.lower().strip()
    # 첨자 문자 → ASCII 변환 (예: ₂ → 2)
    subscript_map = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
    text = text.translate(subscript_map)
    # 괄호, 하이픈, 특수문자 제거 (알파벳+숫자+공백만 유지)
    text = re.sub(r"[^a-z0-9\s]", "", text)
    # 연속 공백 정리
    text = re.sub(r"\s+", " ", text).strip()
    return text


def remove_stopwords(text: str) -> str:
    """학술 불용어 제거"""
    words = text.split()
    filtered = [w for w in words if w not in ACADEMIC_STOPWORDS]
    return " ".join(filtered)


def _preprocess_titles(titles: list[str]) -> list[str]:
    """제목 리스트를 전처리하여 TF-IDF 입력용 텍스트 반환"""
    return [remove_stopwords(clean_title(t)) for t in titles]


def extract_keywords(
    df: pd.DataFrame, top_n: int = 20
) -> list[tuple[str, float]]:
    """DataFrame의 title 컬럼에서 TF-IDF 기반 상위 키워드 추출.

    Returns:
        [(keyword, tfidf_score), ...] 내림차순 정렬
    """
    if df.empty:
        return []

    docs = _preprocess_titles(df["title"].tolist())
    # 빈 문서만 남은 경우
    if not any(doc.strip() for doc in docs):
        return []

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),  # unigram + bigram
        max_features=500,
        min_df=2 if len(docs) >= 5 else 1,  # 최소 문서 빈도
    )
    tfidf_matrix = vectorizer.fit_transform(docs)
    feature_names = vectorizer.get_feature_names_out()

    # 전체 문서에 걸친 평균 TF-IDF 점수
    avg_scores = tfidf_matrix.mean(axis=0).A1
    top_indices = avg_scores.argsort()[::-1][:top_n]

    return [(feature_names[i], float(avg_scores[i])) for i in top_indices]


def extract_keywords_by_month(
    df: pd.DataFrame, top_n: int = 10
) -> dict[str, list[tuple[str, float]]]:
    """월별로 키워드 추출.

    Returns:
        {"YYYY-MM": [(keyword, score), ...], ...}
    """
    if df.empty:
        return {}

    df = df.copy()
    df["month"] = df["date"].dt.strftime("%Y-%m")
    result = {}
    for month, group in df.groupby("month"):
        if pd.isna(month):
            continue
        keywords = extract_keywords(group, top_n=top_n)
        result[month] = keywords
    return result
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
pytest tests/test_preprocessor.py -v
```
Expected: 8 passed

- [ ] **Step 5: 커밋**

```bash
git add analytics/preprocessor.py tests/test_preprocessor.py
git commit -m "feat: Title 전처리 + TF-IDF 키워드 추출 구현"
```

---

### Task 4: Analyzer — 4가지 분석 로직

**Files:**
- Create: `analytics/analyzer.py`
- Create: `tests/test_analyzer.py`

- [ ] **Step 1: failing test 작성**

`tests/test_analyzer.py`:
```python
"""analyzer 단위 테스트 — 4가지 분석 로직"""
import pandas as pd
import pytest


def _sample_df():
    """테스트용 샘플 DataFrame"""
    return pd.DataFrame({
        "title": [
            "oxygen evolution reaction on perovskite",
            "oxygen evolution catalyst nickel iron",
            "carbon dioxide reduction copper",
            "carbon dioxide electrochemical conversion",
            "perovskite solar cell efficiency",
            "lithium ion battery cathode material",
            "hydrogen evolution reaction platinum",
            "oxygen reduction reaction carbon",
        ],
        "journal": [
            "ACS Catalysis", "Nature Catalysis",
            "ACS Catalysis", "Joule",
            "Advanced Materials", "ACS Energy Letters",
            "Nature Catalysis", "ACS Catalysis",
        ],
        "date": pd.to_datetime([
            "2026-01-10", "2026-01-15",
            "2026-01-20", "2026-02-05",
            "2026-02-10", "2026-02-15",
            "2026-03-01", "2026-03-10",
        ]),
        "url": [""] * 8,
        "status": [
            "대기중", "관심", "대기중", "관심",
            "대기중", "읽음", "대기중", "대기중",
        ],
    })


class TestJournalFrequency:
    """저널별 발행 빈도 분석"""

    def test_basic_frequency(self):
        from analytics.analyzer import journal_frequency

        df = _sample_df()
        result = journal_frequency(df)

        # result는 pd.Series (journal → count), 내림차순
        assert result.iloc[0] == 3  # ACS Catalysis가 3건으로 최다
        assert result.index[0] == "ACS Catalysis"

    def test_monthly_frequency(self):
        from analytics.analyzer import journal_monthly_frequency

        df = _sample_df()
        result = journal_monthly_frequency(df)

        # result는 DataFrame (index=month, columns=journal, values=count)
        assert "ACS Catalysis" in result.columns
        assert "2026-01" in result.index


class TestKeywordTrend:
    """키워드 트렌드 분석"""

    def test_trend_structure(self):
        from analytics.analyzer import keyword_trend

        df = _sample_df()
        result = keyword_trend(df, top_n=5)

        # result는 DataFrame (index=month, columns=keyword, values=frequency)
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) <= 5


class TestJournalKeywordCrosstab:
    """저널 x 키워드 크로스탭"""

    def test_crosstab_structure(self):
        from analytics.analyzer import journal_keyword_crosstab

        df = _sample_df()
        result = journal_keyword_crosstab(df, top_n_keywords=5)

        # result는 DataFrame (index=journal, columns=keyword, values=count)
        assert isinstance(result, pd.DataFrame)
        assert len(result.columns) <= 5


class TestInterestAnalysis:
    """AI 관심 논문 분석"""

    def test_interest_ratio_by_journal(self):
        from analytics.analyzer import interest_ratio_by_journal

        df = _sample_df()
        result = interest_ratio_by_journal(df)

        # result는 DataFrame (journal, total, interest, ratio)
        assert "journal" in result.columns
        assert "ratio" in result.columns
        # Nature Catalysis: 2건 중 1건 관심 → 0.5
        nc = result[result["journal"] == "Nature Catalysis"]
        assert nc.iloc[0]["ratio"] == 0.5

    def test_interest_keywords(self):
        from analytics.analyzer import interest_keywords

        df = _sample_df()
        all_kw, interest_kw = interest_keywords(df, top_n=5)

        assert isinstance(all_kw, list)
        assert isinstance(interest_kw, list)
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_analyzer.py -v
```
Expected: FAIL — `analytics.analyzer` 모듈 없음

- [ ] **Step 3: analyzer.py 구현**

`analytics/analyzer.py`:
```python
"""4가지 분석 로직: 키워드 트렌드, 저널×키워드, AI 관심 분석, 저널 빈도.

모든 함수는 pandas DataFrame을 입력받아 분석 결과를 반환한다.
"""
import pandas as pd

from analytics.preprocessor import extract_keywords, clean_title, remove_stopwords


def journal_frequency(df: pd.DataFrame) -> pd.Series:
    """저널별 발행 빈도 (내림차순 Series)"""
    return df["journal"].value_counts()


def journal_monthly_frequency(df: pd.DataFrame) -> pd.DataFrame:
    """월별 × 저널별 발행 빈도 피벗 테이블.

    Returns:
        DataFrame (index=YYYY-MM, columns=journal, values=count)
    """
    df = df.copy()
    df["month"] = df["date"].dt.strftime("%Y-%m")
    pivot = df.pivot_table(
        index="month", columns="journal", values="title",
        aggfunc="count", fill_value=0,
    )
    return pivot.sort_index()


def keyword_trend(df: pd.DataFrame, top_n: int = 10) -> pd.DataFrame:
    """월별 키워드 빈도 변화 추적.

    전체 코퍼스에서 top_n 키워드를 뽑고, 각 월별 등장 횟수를 카운트.

    Returns:
        DataFrame (index=YYYY-MM, columns=keyword, values=count)
    """
    # 전체에서 top 키워드 추출
    top_keywords = extract_keywords(df, top_n=top_n)
    if not top_keywords:
        return pd.DataFrame()

    keyword_names = [kw[0] for kw in top_keywords]

    df = df.copy()
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["clean_title"] = df["title"].apply(lambda t: remove_stopwords(clean_title(t)))

    # 각 월별로 키워드 등장 횟수 카운트
    records = []
    for month, group in df.groupby("month"):
        if pd.isna(month):
            continue
        row = {"month": month}
        titles_text = " ".join(group["clean_title"].tolist())
        for kw in keyword_names:
            # 키워드가 제목 텍스트에 등장하는 횟수
            row[kw] = sum(1 for t in group["clean_title"] if kw in t)
        records.append(row)

    result = pd.DataFrame(records).set_index("month").sort_index()
    return result


def journal_keyword_crosstab(
    df: pd.DataFrame, top_n_keywords: int = 15
) -> pd.DataFrame:
    """저널 × 키워드 크로스탭 (히트맵용).

    Returns:
        DataFrame (index=journal, columns=keyword, values=count)
    """
    top_keywords = extract_keywords(df, top_n=top_n_keywords)
    if not top_keywords:
        return pd.DataFrame()

    keyword_names = [kw[0] for kw in top_keywords]

    df = df.copy()
    df["clean_title"] = df["title"].apply(lambda t: remove_stopwords(clean_title(t)))

    records = []
    for journal, group in df.groupby("journal"):
        if not journal:
            continue
        row = {"journal": journal}
        for kw in keyword_names:
            row[kw] = sum(1 for t in group["clean_title"] if kw in t)
        records.append(row)

    result = pd.DataFrame(records).set_index("journal")
    return result


def interest_ratio_by_journal(df: pd.DataFrame) -> pd.DataFrame:
    """저널별 AI 관심 논문 비율.

    status != '대기중'인 논문을 관심 논문으로 분류.

    Returns:
        DataFrame (journal, total, interest, ratio) — ratio 내림차순
    """
    total = df.groupby("journal").size().reset_index(name="total")
    interest = (
        df[df["status"] != "대기중"]
        .groupby("journal")
        .size()
        .reset_index(name="interest")
    )
    result = total.merge(interest, on="journal", how="left").fillna(0)
    result["interest"] = result["interest"].astype(int)
    result["ratio"] = result["interest"] / result["total"]
    return result.sort_values("ratio", ascending=False).reset_index(drop=True)


def interest_keywords(
    df: pd.DataFrame, top_n: int = 15
) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
    """전체 키워드 vs AI 관심 논문 키워드 비교.

    Returns:
        (all_keywords, interest_keywords) — 각각 [(keyword, score), ...]
    """
    all_kw = extract_keywords(df, top_n=top_n)
    interest_df = df[df["status"] != "대기중"]
    interest_kw = extract_keywords(interest_df, top_n=top_n) if not interest_df.empty else []
    return all_kw, interest_kw
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
pytest tests/test_analyzer.py -v
```
Expected: 6 passed

- [ ] **Step 5: 커밋**

```bash
git add analytics/analyzer.py tests/test_analyzer.py
git commit -m "feat: 4가지 분석 로직 구현 (트렌드, 크로스탭, 관심, 빈도)"
```

---

### Task 5: Report — 마크다운 리포트 생성

**Files:**
- Create: `analytics/report.py`
- Create: `tests/test_report.py`

- [ ] **Step 1: failing test 작성**

`tests/test_report.py`:
```python
"""report 단위 테스트 — 마크다운 리포트 생성"""
import pandas as pd
import pytest


def _sample_df():
    return pd.DataFrame({
        "title": [
            "oxygen evolution reaction on perovskite",
            "carbon dioxide reduction copper",
            "perovskite solar cell efficiency",
        ],
        "journal": ["ACS Catalysis", "Nature Catalysis", "Advanced Materials"],
        "date": pd.to_datetime(["2026-01-10", "2026-01-15", "2026-02-10"]),
        "url": ["", "", ""],
        "status": ["대기중", "관심", "대기중"],
    })


class TestGenerateReport:
    """마크다운 리포트 생성"""

    def test_report_contains_sections(self):
        from analytics.report import generate_report

        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")

        assert "# get-ASAP 논문 분석 리포트" in md
        assert "키워드 트렌드" in md
        assert "저널 통계" in md

    def test_report_has_date_range(self):
        from analytics.report import generate_report

        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")

        assert "2026-01" in md
        assert "2026-02" in md

    def test_save_report(self, tmp_path):
        from analytics.report import generate_report, save_report

        df = _sample_df()
        md = generate_report(df, start="2026-01", end="2026-02")
        path = save_report(md, "2026-01", "2026-02", reports_dir=str(tmp_path))

        assert path.endswith(".md")
        with open(path, encoding="utf-8") as f:
            content = f.read()
        assert "get-ASAP" in content
```

- [ ] **Step 2: 테스트 실행 — 실패 확인**

```bash
pytest tests/test_report.py -v
```
Expected: FAIL — `analytics.report` 모듈 없음

- [ ] **Step 3: report.py 구현**

`analytics/report.py`:
```python
"""마크다운 리포트 생성.

현재 분석 결과를 마크다운 형식으로 정리하여 파일로 저장한다.
"""
import os
from datetime import datetime

import pandas as pd

from analytics.preprocessor import extract_keywords
from analytics.analyzer import (
    journal_frequency,
    interest_ratio_by_journal,
    interest_keywords,
)

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "reports")


def generate_report(df: pd.DataFrame, start: str, end: str) -> str:
    """분석 결과를 마크다운 리포트로 생성.

    Args:
        df: 논문 DataFrame
        start: 시작 월 (YYYY-MM)
        end: 종료 월 (YYYY-MM)

    Returns:
        마크다운 문자열
    """
    total = len(df)
    journals = df["journal"].nunique()
    interest_count = len(df[df["status"] != "대기중"])

    lines = [
        f"# get-ASAP 논문 분석 리포트",
        f"",
        f"- **기간**: {start} ~ {end}",
        f"- **총 논문 수**: {total}건",
        f"- **저널 수**: {journals}개",
        f"- **AI 관심 논문**: {interest_count}건 ({interest_count/total*100:.1f}%)" if total > 0 else f"- **AI 관심 논문**: 0건",
        f"- **생성일**: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"",
    ]

    # 키워드 트렌드
    lines.append("## 키워드 트렌드 (Top 15)")
    lines.append("")
    top_kw = extract_keywords(df, top_n=15)
    if top_kw:
        lines.append("| 순위 | 키워드 | TF-IDF 점수 |")
        lines.append("|------|--------|-------------|")
        for i, (kw, score) in enumerate(top_kw, 1):
            lines.append(f"| {i} | {kw} | {score:.4f} |")
    else:
        lines.append("데이터 부족으로 키워드를 추출할 수 없습니다.")
    lines.append("")

    # 저널 통계
    lines.append("## 저널 통계 (Top 10)")
    lines.append("")
    freq = journal_frequency(df).head(10)
    if not freq.empty:
        lines.append("| 저널 | 논문 수 |")
        lines.append("|------|---------|")
        for journal, count in freq.items():
            lines.append(f"| {journal} | {count} |")
    lines.append("")

    # AI 관심 분석
    lines.append("## AI 관심 논문 분석")
    lines.append("")
    ratio_df = interest_ratio_by_journal(df)
    top_interest = ratio_df[ratio_df["interest"] > 0].head(10)
    if not top_interest.empty:
        lines.append("| 저널 | 전체 | 관심 | 비율 |")
        lines.append("|------|------|------|------|")
        for _, row in top_interest.iterrows():
            lines.append(
                f"| {row['journal']} | {row['total']} | {row['interest']} | {row['ratio']:.0%} |"
            )
    else:
        lines.append("AI 관심 논문이 없습니다.")
    lines.append("")

    # 관심 키워드 비교
    all_kw, int_kw = interest_keywords(df, top_n=10)
    if int_kw:
        lines.append("### 관심 논문 키워드 vs 전체 키워드")
        lines.append("")
        lines.append("| 전체 Top 키워드 | 관심 논문 Top 키워드 |")
        lines.append("|-----------------|---------------------|")
        max_len = max(len(all_kw), len(int_kw))
        for i in range(max_len):
            a = all_kw[i][0] if i < len(all_kw) else ""
            b = int_kw[i][0] if i < len(int_kw) else ""
            lines.append(f"| {a} | {b} |")
    lines.append("")

    return "\n".join(lines)


def save_report(
    md: str, start: str, end: str, reports_dir: str = None
) -> str:
    """리포트를 파일로 저장.

    Returns:
        저장된 파일 경로
    """
    reports_dir = reports_dir or REPORTS_DIR
    os.makedirs(reports_dir, exist_ok=True)
    filename = f"{start}_to_{end}_analysis.md"
    path = os.path.join(reports_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
```

- [ ] **Step 4: 테스트 실행 — 통과 확인**

```bash
pytest tests/test_report.py -v
```
Expected: 3 passed

- [ ] **Step 5: 커밋**

```bash
git add analytics/report.py tests/test_report.py
git commit -m "feat: 마크다운 리포트 생성 기능 구현"
```

---

### Task 6: Streamlit 대시보드

**Files:**
- Create: `dashboard/app.py`

- [ ] **Step 1: Streamlit 앱 구현**

`dashboard/app.py`:
```python
"""get-ASAP 논문 분석 대시보드.

실행: streamlit run dashboard/app.py
"""
import sys
import os
from datetime import date

# 프로젝트 루트를 path에 추가 (상대 import 대신)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib
import seaborn as sns
from wordcloud import WordCloud

from analytics.notion_fetcher import fetch_papers
from analytics.preprocessor import extract_keywords, extract_keywords_by_month
from analytics.analyzer import (
    journal_frequency,
    journal_monthly_frequency,
    keyword_trend,
    journal_keyword_crosstab,
    interest_ratio_by_journal,
    interest_keywords,
)
from analytics.report import generate_report, save_report

# 한글 폰트 설정 (Windows)
matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

st.set_page_config(page_title="get-ASAP Analytics", layout="wide")
st.title("get-ASAP 논문 분석 대시보드")


# --- 사이드바: 기간 선택 + 필터 ---
st.sidebar.header("설정")

today = date.today()
default_start = today.replace(month=max(1, today.month - 5))  # 최근 6개월
start_month = st.sidebar.text_input(
    "시작 월 (YYYY-MM)", value=default_start.strftime("%Y-%m")
)
end_month = st.sidebar.text_input(
    "종료 월 (YYYY-MM)", value=today.strftime("%Y-%m")
)

force_refresh = st.sidebar.button("데이터 갱신 (Notion re-fetch)")


# --- 데이터 로드 ---
@st.cache_data(ttl=3600, show_spinner="Notion에서 데이터 로드 중...")
def load_data(start: str, end: str, _force: bool = False) -> pd.DataFrame:
    return fetch_papers(start, end, force_refresh=_force)


try:
    df = load_data(start_month, end_month, force_refresh)
except Exception as e:
    st.error(f"데이터 로드 실패: {e}")
    st.stop()

if df.empty:
    st.warning("선택한 기간에 데이터가 없습니다.")
    st.stop()

# --- 사이드바: 저널/키워드 필터 ---
all_journals = sorted(df["journal"].unique())
selected_journals = st.sidebar.multiselect(
    "저널 필터", options=all_journals, default=all_journals
)

keyword_filter = st.sidebar.text_input("키워드 검색 (Title 포함 필터)", value="")

# 필터 적용
filtered = df[df["journal"].isin(selected_journals)]
if keyword_filter:
    filtered = filtered[
        filtered["title"].str.contains(keyword_filter, case=False, na=False)
    ]

st.sidebar.markdown(f"**필터 결과: {len(filtered)}건** / 전체 {len(df)}건")

# --- 탭 ---
tab1, tab2, tab3, tab4 = st.tabs([
    "키워드 트렌드", "저널 x 키워드", "AI 관심 분석", "저널 통계"
])


# === 탭 1: 키워드 트렌드 ===
with tab1:
    st.header("키워드 트렌드")

    col1, col2 = st.columns([2, 1])

    with col1:
        top_n = st.slider("표시할 키워드 수", 5, 20, 10, key="trend_topn")
        trend_df = keyword_trend(filtered, top_n=top_n)
        if not trend_df.empty:
            fig, ax = plt.subplots(figsize=(12, 6))
            trend_df.plot(ax=ax, marker="o", linewidth=2)
            ax.set_xlabel("월")
            ax.set_ylabel("논문 수")
            ax.set_title("월별 키워드 빈도 변화")
            ax.legend(bbox_to_anchor=(1.05, 1), loc="upper left", fontsize=8)
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()
        else:
            st.info("키워드 트렌드를 생성할 데이터가 부족합니다.")

    with col2:
        st.subheader("워드클라우드")
        keywords = extract_keywords(filtered, top_n=50)
        if keywords:
            word_freq = {kw: score for kw, score in keywords}
            wc = WordCloud(
                width=600, height=400,
                background_color="white",
                colormap="viridis",
            ).generate_from_frequencies(word_freq)
            fig, ax = plt.subplots(figsize=(6, 4))
            ax.imshow(wc, interpolation="bilinear")
            ax.axis("off")
            st.pyplot(fig)
            plt.close()
        else:
            st.info("키워드를 추출할 데이터가 부족합니다.")

    # 키워드 상세 테이블
    st.subheader("Top 키워드 상세")
    keywords = extract_keywords(filtered, top_n=20)
    if keywords:
        kw_df = pd.DataFrame(keywords, columns=["키워드", "TF-IDF 점수"])
        kw_df.index = range(1, len(kw_df) + 1)
        kw_df.index.name = "순위"
        st.dataframe(kw_df, use_container_width=True)


# === 탭 2: 저널 x 키워드 ===
with tab2:
    st.header("저널 x 키워드 크로스탭")

    top_n_ct = st.slider("키워드 수", 5, 20, 10, key="ct_topn")
    ct_df = journal_keyword_crosstab(filtered, top_n_keywords=top_n_ct)
    if not ct_df.empty:
        fig, ax = plt.subplots(figsize=(14, max(6, len(ct_df) * 0.4)))
        sns.heatmap(
            ct_df, annot=True, fmt="d", cmap="YlOrRd",
            linewidths=0.5, ax=ax,
        )
        ax.set_title("저널별 키워드 등장 빈도")
        ax.set_ylabel("")
        plt.tight_layout()
        st.pyplot(fig)
        plt.close()
    else:
        st.info("크로스탭을 생성할 데이터가 부족합니다.")

    # 특정 키워드 → 저널 랭킹
    st.subheader("키워드 → 저널 랭킹")
    search_kw = st.text_input("키워드를 입력하세요", value="", key="kw_search")
    if search_kw:
        from analytics.preprocessor import clean_title, remove_stopwords

        filtered_copy = filtered.copy()
        filtered_copy["clean_title"] = filtered_copy["title"].apply(
            lambda t: remove_stopwords(clean_title(t))
        )
        matched = filtered_copy[
            filtered_copy["clean_title"].str.contains(search_kw.lower(), na=False)
        ]
        if not matched.empty:
            rank = matched["journal"].value_counts()
            st.bar_chart(rank)
            st.write(f"**'{search_kw}' 포함 논문: {len(matched)}건**")
        else:
            st.warning(f"'{search_kw}'를 포함하는 논문이 없습니다.")


# === 탭 3: AI 관심 분석 ===
with tab3:
    st.header("AI 관심 논문 분석")

    interest_count = len(filtered[filtered["status"] != "대기중"])
    total_count = len(filtered)
    st.metric(
        "AI 관심 논문 비율",
        f"{interest_count}/{total_count}",
        f"{interest_count/total_count*100:.1f}%" if total_count > 0 else "0%",
    )

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("저널별 관심 비율")
        ratio_df = interest_ratio_by_journal(filtered)
        top_ratio = ratio_df[ratio_df["interest"] > 0]
        if not top_ratio.empty:
            fig, ax = plt.subplots(figsize=(10, max(4, len(top_ratio) * 0.35)))
            bars = ax.barh(top_ratio["journal"], top_ratio["ratio"])
            ax.set_xlabel("관심 비율")
            ax.set_title("저널별 AI 관심 논문 비율")
            # 비율 라벨 표시
            for bar, ratio in zip(bars, top_ratio["ratio"]):
                ax.text(bar.get_width() + 0.01, bar.get_y() + bar.get_height() / 2,
                        f"{ratio:.0%}", va="center", fontsize=9)
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()
        else:
            st.info("AI 관심 논문이 없습니다.")

    with col2:
        st.subheader("전체 vs 관심 키워드 비교")
        all_kw, int_kw = interest_keywords(filtered, top_n=10)
        if all_kw and int_kw:
            compare_df = pd.DataFrame({
                "전체 Top 키워드": [kw[0] for kw in all_kw[:10]],
                "관심 논문 Top 키워드": [kw[0] for kw in int_kw[:10]] + [""] * max(0, 10 - len(int_kw)),
            })
            st.dataframe(compare_df, use_container_width=True, hide_index=True)
        elif all_kw:
            st.info("AI 관심 논문이 부족하여 비교할 수 없습니다.")
        else:
            st.info("키워드를 추출할 데이터가 부족합니다.")


# === 탭 4: 저널 통계 ===
with tab4:
    st.header("저널 통계")

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("저널별 발행 빈도")
        freq = journal_frequency(filtered).head(15)
        if not freq.empty:
            fig, ax = plt.subplots(figsize=(10, max(4, len(freq) * 0.35)))
            freq.sort_values().plot.barh(ax=ax, color="steelblue")
            ax.set_xlabel("논문 수")
            ax.set_title("저널별 발행 빈도 (Top 15)")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()

    with col2:
        st.subheader("월별 활동 트렌드")
        monthly = journal_monthly_frequency(filtered)
        if not monthly.empty:
            # 전체 월별 합계
            monthly_total = monthly.sum(axis=1)
            fig, ax = plt.subplots(figsize=(10, 4))
            monthly_total.plot(ax=ax, marker="o", linewidth=2, color="steelblue")
            ax.set_xlabel("월")
            ax.set_ylabel("논문 수")
            ax.set_title("월별 총 발행 논문 수")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()

    # 저널별 월별 상세 (확장 가능)
    st.subheader("저널별 월별 상세")
    monthly = journal_monthly_frequency(filtered)
    if not monthly.empty:
        st.dataframe(monthly, use_container_width=True)


# --- 리포트 다운로드 ---
st.markdown("---")
st.header("리포트 다운로드")

if st.button("마크다운 리포트 생성"):
    md = generate_report(filtered, start=start_month, end=end_month)
    path = save_report(md, start_month, end_month)
    st.success(f"리포트 저장: {path}")
    st.download_button(
        label="리포트 다운로드 (.md)",
        data=md,
        file_name=f"{start_month}_to_{end_month}_analysis.md",
        mime="text/markdown",
    )
```

- [ ] **Step 2: 로컬 실행 테스트**

```bash
streamlit run dashboard/app.py
```
Expected: 브라우저에서 대시보드 열림, Notion에서 데이터 로드 시도

- [ ] **Step 3: 커밋**

```bash
git add dashboard/app.py
git commit -m "feat: Streamlit 논문 분석 대시보드 구현"
```

---

### Task 7: 통합 테스트 + 마무리

**Files:**
- Modify: `dashboard/app.py` (필요시 버그 수정)

- [ ] **Step 1: 전체 테스트 실행**

```bash
pytest tests/test_notion_fetcher.py tests/test_preprocessor.py tests/test_analyzer.py tests/test_report.py -v
```
Expected: 전체 통과

- [ ] **Step 2: Streamlit 실행 및 E2E 확인**

```bash
streamlit run dashboard/app.py
```
확인 사항:
1. 사이드바에서 기간 선택 → 데이터 로드
2. 탭 1: 키워드 트렌드 라인차트 + 워드클라우드
3. 탭 2: 히트맵 + 키워드 검색
4. 탭 3: AI 관심 비율 + 키워드 비교
5. 탭 4: 저널 바차트 + 월별 트렌드
6. 리포트 생성 및 다운로드

- [ ] **Step 3: 최종 커밋**

```bash
git add -A
git commit -m "test: analytics 전체 테스트 통과 확인"
```
