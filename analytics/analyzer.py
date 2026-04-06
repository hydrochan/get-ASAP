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
    top_keywords = extract_keywords(df, top_n=top_n)
    if not top_keywords:
        return pd.DataFrame()

    keyword_names = [kw[0] for kw in top_keywords]

    df = df.copy()
    df["month"] = df["date"].dt.strftime("%Y-%m")
    df["clean_title"] = df["title"].apply(lambda t: remove_stopwords(clean_title(t)))

    records = []
    for month, group in df.groupby("month"):
        if pd.isna(month):
            continue
        row = {"month": month}
        for kw in keyword_names:
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
