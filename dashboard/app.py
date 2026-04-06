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
from streamlit_google_auth import Authenticate

import config
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

# --- Google 로그인 인증 ---
authenticator = Authenticate(
    secret_credentials_path="credentials.json",
    cookie_name="get_asap_auth",
    redirect_uri="http://localhost:8501",  # 배포 시 서버 URL로 변경
)
authenticator.check_authenticity()

if not st.session_state.get("connected"):
    st.title("get-ASAP 논문 분석 대시보드")
    st.info("Google 로그인이 필요합니다.")
    authenticator.login()
    st.stop()

# 허용된 이메일만 통과
user_email = st.session_state.get("user_info", {}).get("email", "")
if user_email not in config.DASHBOARD_ALLOWED_EMAILS:
    st.error(f"접근 권한이 없습니다: {user_email}")
    authenticator.logout()
    st.stop()

st.title("get-ASAP 논문 분석 대시보드")
st.sidebar.markdown(f"**{user_email}** 로그인됨")
if st.sidebar.button("로그아웃"):
    authenticator.logout()

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
except Exception:
    st.error("데이터 로드에 실패했습니다. 잠시 후 다시 시도해주세요.")
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
            monthly_total = monthly.sum(axis=1)
            fig, ax = plt.subplots(figsize=(10, 4))
            monthly_total.plot(ax=ax, marker="o", linewidth=2, color="steelblue")
            ax.set_xlabel("월")
            ax.set_ylabel("논문 수")
            ax.set_title("월별 총 발행 논문 수")
            plt.tight_layout()
            st.pyplot(fig)
            plt.close()

    st.subheader("저널별 월별 상세")
    monthly = journal_monthly_frequency(filtered)
    if not monthly.empty:
        st.dataframe(monthly, use_container_width=True)


# --- 리포트 다운로드 ---
st.markdown("---")
st.header("리포트 다운로드")

if st.button("마크다운 리포트 생성"):
    md = generate_report(filtered, start=start_month, end=end_month)
    save_report(md, start_month, end_month)
    st.success("리포트가 생성되었습니다.")
    st.download_button(
        label="리포트 다운로드 (.md)",
        data=md,
        file_name=f"{start_month}_to_{end_month}_analysis.md",
        mime="text/markdown",
    )
