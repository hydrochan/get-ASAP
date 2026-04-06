# get-ASAP Paper Analytics Design

## Overview

Notion에 축적된 논문 데이터(Title, Journal, Date, Status)를 기반으로 키워드 트렌드, 저널별 주제 분포, AI 관심 논문 패턴을 분석하는 Streamlit 대시보드.

## Constraints

- Title 텍스트만으로 분석 (Abstract는 향후 점진적 확장)
- AI/ML은 TF-IDF, 토픽 모델링 수준까지만 (딥러닝 없음)
- 기존 get-ASAP 프로젝트에 `analytics/`, `dashboard/` 디렉토리 추가
- Python 기반, 기존 의존성(notion-client, python-dotenv)과 공존

## Data Pipeline

### Notion → DataFrame

1. 사용자가 기간 선택 (YYYY-MM ~ YYYY-MM)
2. `_find_monthly_db()` 재활용하여 해당 월별 DB들 탐색
3. 각 DB에서 `data_sources.query()`로 전체 페이지를 pagination하며 fetch
4. pandas DataFrame으로 변환:

| column   | type     | source                |
|----------|----------|-----------------------|
| title    | str      | Title property        |
| journal  | str      | Journal select        |
| date     | datetime | Date property         |
| url      | str      | URL property          |
| status   | str      | Status select         |

### Caching

- 최초 fetch 후 `cache/papers_YYYY-MM.csv`로 저장
- 이후 실행 시 캐시 파일 존재하면 로컬에서 로드
- "데이터 갱신" 버튼으로 강제 re-fetch 가능
- 현재 월(진행 중)은 항상 re-fetch

## Analysis Engine

### 1. 키워드 트렌드 (우선순위 1)

- **전처리**: 소문자화, 학술 불용어 제거 (영어 기본 + "study", "effect", "novel", "high-performance", "based", "using", "via" 등 커스텀)
- **TF-IDF**: scikit-learn `TfidfVectorizer`로 unigram + bigram 추출
- **출력**: 월별 Top-N 키워드 빈도 변화 라인차트, 워드클라우드

### 2. 저널 x 키워드 크로스탭 (우선순위 2)

- TF-IDF 결과에서 상위 키워드 추출
- 저널별로 해당 키워드가 포함된 논문 수 집계
- **출력**: 히트맵 (저널 vs 키워드), 특정 키워드 → 저널 랭킹

### 3. AI 관심 논문 분석 (우선순위 3)

- `status != "대기중"` 필터로 AI가 관심 논문으로 판별한 데이터 분리
- 관심 논문과 전체 논문의 키워드 분포 비교
- **출력**: 저널별 관심 비율 바차트, 관심 키워드 vs 전체 키워드 비교

### 4. 저널 발행 빈도 (우선순위 4)

- journal + date로 저널별 발행 건수 집계
- **출력**: 저널별 바차트 + 월별 트렌드 라인차트

## Streamlit Dashboard

### Layout

```
Sidebar                     Main Area
+---------------+           +----------------------------+
| Period Select |           | Tab 1: Keyword Trends      |
| (YYYY-MM ~)   |           |  - Monthly top-N line chart |
|               |           |  - Word cloud              |
| Refresh Data  |           +----------------------------+
|               |           | Tab 2: Journal x Keyword   |
| Journal Filter|           |  - Heatmap                 |
| (multiselect) |           |  - Keyword -> journal rank |
|               |           +----------------------------+
| Keyword Filter|           | Tab 3: AI Interest         |
| (search/pick) |           |  - Interest ratio by journal|
|               |           |  - Interest vs all keywords|
|               |           +----------------------------+
|               |           | Tab 4: Journal Stats       |
|               |           |  - Frequency bar chart     |
|               |           |  - Monthly activity trend  |
|               |           +----------------------------+
|               |           | Report Download            |
|               |           | (Markdown / HTML export)   |
+---------------+           +----------------------------+
```

### Entry Point

`streamlit run dashboard/app.py`

## Report Generation

- 대시보드 하단 "리포트 생성" 버튼 → 현재 필터 기준으로 마크다운 요약 생성
- `reports/YYYY-MM-analysis.md`로 저장
- 선택적 cron 연동: 월 1회 자동 리포트 생성 가능

## Project Structure

```
get-ASAP/
├── (existing files)
├── analytics/
│   ├── __init__.py
│   ├── notion_fetcher.py    # Notion -> DataFrame + caching
│   ├── preprocessor.py      # Text preprocessing + TF-IDF
│   ├── analyzer.py          # Analysis logic (trends, crosstab, interest)
│   └── report.py            # Markdown report generation
├── dashboard/
│   └── app.py               # Streamlit app entry point
├── cache/                   # CSV cache (gitignored)
│   └── papers_YYYY-MM.csv
└── reports/                 # Generated reports (gitignored)
    └── YYYY-MM-analysis.md
```

## Dependencies (additions to requirements.txt)

```
streamlit
pandas
scikit-learn
matplotlib
seaborn
wordcloud
```

## Future Extensions

- Abstract 수집 (CrossRef API) → 심층 토픽 모델링
- BERTopic 기반 자동 토픽 클러스터링
- 저자 네트워크 분석
- 인용 수 기반 임팩트 예측
