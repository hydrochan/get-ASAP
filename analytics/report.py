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
    """분석 결과를 마크다운 리포트로 생성."""
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
    """리포트를 파일로 저장. Returns 저장된 파일 경로."""
    reports_dir = reports_dir or REPORTS_DIR
    os.makedirs(reports_dir, exist_ok=True)
    filename = f"{start}_to_{end}_analysis.md"
    path = os.path.join(reports_dir, filename)
    with open(path, "w", encoding="utf-8") as f:
        f.write(md)
    return path
