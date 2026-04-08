"""Title 전처리 및 TF-IDF 키워드 추출.

논문 제목에서 의미 있는 키워드를 추출하고 빈도/중요도를 분석한다.
"""
import re

import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer

# 영어 기본 불용어 + 학술 논문 공통어
ACADEMIC_STOPWORDS = {
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
    "dual", "phase", "acid", "ion", "single",
    # 하이픈 제거 후 남는 잔여 토큰 (co-catalyst → "co", de-novo → "de" 등)
    "co", "de", "non", "re", "pre", "post", "ex", "multi", "sub",
}


def clean_title(title: str) -> str:
    """제목 전처리: 소문자화, 특수문자 제거"""
    if not title:
        return ""
    text = title.lower().strip()
    # 유니코드 아래첨자 숫자를 일반 숫자로 변환
    subscript_map = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")
    text = text.translate(subscript_map)
    # 영문, 숫자, 공백만 남기고 제거
    text = re.sub(r"[^a-z0-9\s]", "", text)
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
    # 전처리 후 빈 문서만 남은 경우
    if not any(doc.strip() for doc in docs):
        return []

    vectorizer = TfidfVectorizer(
        ngram_range=(1, 2),
        max_features=500,
        min_df=2 if len(docs) >= 5 else 1,
    )
    tfidf_matrix = vectorizer.fit_transform(docs)
    feature_names = vectorizer.get_feature_names_out()

    # 전체 문서에 대한 평균 TF-IDF 스코어 계산
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
