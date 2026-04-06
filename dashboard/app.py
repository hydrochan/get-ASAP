"""get-ASAP 논문 분석 대시보드.

실행: streamlit run dashboard/app.py
"""
import sys
import os
from datetime import date

# 프로젝트 루트를 path에 추가
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import streamlit as st
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from wordcloud import WordCloud
import matplotlib.pyplot as plt
import matplotlib
import bcrypt
import time as _time

import config
from analytics.notion_fetcher import fetch_papers
from analytics.preprocessor import extract_keywords, clean_title, remove_stopwords
from analytics.analyzer import (
    journal_frequency,
    journal_monthly_frequency,
    keyword_trend,
    journal_keyword_crosstab,
    interest_ratio_by_journal,
    interest_keywords,
)
from analytics.report import generate_report, save_report

# 한글 폰트 설정 (matplotlib — 워드클라우드용)
matplotlib.rcParams["font.family"] = "Malgun Gothic"
matplotlib.rcParams["axes.unicode_minus"] = False

# --- 페이지 설정 + 커스텀 CSS ---
st.set_page_config(
    page_title="get-ASAP Analytics",
    page_icon="📊",
    layout="wide",
)

# CSS 파일에서 스타일 로드
_css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
with open(_css_path, encoding="utf-8") as _f:
    st.markdown(f"<style>{_f.read()}</style>", unsafe_allow_html=True)

# --- 비밀번호 인증 ---
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 300
SESSION_TIMEOUT_SECONDS = 3600


def check_password() -> bool:
    """비밀번호 인증 게이트. 브루트포스 방지 + 세션 타임아웃."""
    if st.session_state.get("authenticated"):
        login_time = st.session_state.get("login_time", 0)
        if _time.time() - login_time > SESSION_TIMEOUT_SECONDS:
            st.session_state["authenticated"] = False
            st.warning("세션이 만료되었습니다. 다시 로그인해주세요.")
        else:
            return True

    attempts = st.session_state.get("login_attempts", 0)
    lockout_until = st.session_state.get("lockout_until", 0)
    if _time.time() < lockout_until:
        remaining = int(lockout_until - _time.time())
        st.error(f"로그인 시도 횟수 초과. {remaining}초 후 다시 시도해주세요.")
        return False

    # 로그인 화면 — 중앙 정렬
    _, center, _ = st.columns([1, 2, 1])
    with center:
        st.markdown("## 📊 get-ASAP Analytics")
        st.markdown("논문 트렌드 분석 대시보드")
        with st.form("login_form"):
            username = st.text_input("ID")
            password = st.text_input("비밀번호", type="password")
            submitted = st.form_submit_button("로그인", use_container_width=True)

        if submitted:
            if (
                username == config.DASHBOARD_USERNAME
                and config.DASHBOARD_PASSWORD_HASH
                and bcrypt.checkpw(
                    password.encode(), config.DASHBOARD_PASSWORD_HASH.encode()
                )
            ):
                st.session_state["authenticated"] = True
                st.session_state["username"] = username
                st.session_state["login_time"] = _time.time()
                st.session_state["login_attempts"] = 0
                st.rerun()
            else:
                attempts += 1
                st.session_state["login_attempts"] = attempts
                if attempts >= MAX_LOGIN_ATTEMPTS:
                    st.session_state["lockout_until"] = _time.time() + LOCKOUT_SECONDS
                    st.error(f"로그인 시도 횟수 초과. {LOCKOUT_SECONDS // 60}분간 잠금됩니다.")
                else:
                    st.error(f"ID 또는 비밀번호가 올바르지 않습니다. ({attempts}/{MAX_LOGIN_ATTEMPTS})")
    return False


if not check_password():
    st.stop()

# --- 사이드바 ---
st.sidebar.markdown(f"**{st.session_state['username']}** 로그인됨")
if st.sidebar.button("로그아웃"):
    st.session_state["authenticated"] = False
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("기간 설정")

today = date.today()
default_start = today.replace(month=max(1, today.month - 5))
start_month = st.sidebar.text_input(
    "시작 월 (YYYY-MM)", value=default_start.strftime("%Y-%m")
)
end_month = st.sidebar.text_input(
    "종료 월 (YYYY-MM)", value=today.strftime("%Y-%m")
)
force_refresh = st.sidebar.button("🔄 데이터 갱신")

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

# --- 사이드바: 필터 ---
st.sidebar.markdown("---")
st.sidebar.header("필터")

all_journals = sorted(df["journal"].unique())
selected_journals = st.sidebar.multiselect(
    "저널 필터", options=all_journals, default=all_journals
)
keyword_filter = st.sidebar.text_input("키워드 검색", value="")

filtered = df[df["journal"].isin(selected_journals)]
if keyword_filter:
    filtered = filtered[
        filtered["title"].str.contains(keyword_filter, case=False, na=False)
    ]

st.sidebar.markdown(f"**{len(filtered)}건** / 전체 {len(df)}건")


# ======================================================
# 메인 대시보드 (단일 스크롤 페이지)
# ======================================================

# --- 헤더 ---
st.markdown('<div class="dash-h"><h1>Dashboard</h1>'
            f'<p>Period: {start_month} — {end_month}</p></div>', unsafe_allow_html=True)

# --- KPI 스트립 ---
interest_count = len(filtered[filtered["status"] != "대기중"])
total_count = len(filtered)
top_kw_list = extract_keywords(filtered, top_n=1)
top_keyword = top_kw_list[0][0] if top_kw_list else "-"

k1, k2, k3, k4 = st.columns(4)
k1.metric("총 논문", f"{total_count:,}건")
k2.metric("활성 저널", f"{filtered['journal'].nunique()}개")
k3.metric("Top 키워드", top_keyword)
k4.metric("AI 관심", f"{interest_count}건", f"{interest_count/total_count*100:.1f}%" if total_count > 0 else "0%")


# --- 섹션 1: 키워드 트렌드 ---
st.markdown('<hr>', unsafe_allow_html=True)
st.markdown('<p class="sec-head">키워드 트렌드</p>', unsafe_allow_html=True)

col_chart, col_cloud = st.columns([2, 1])

with col_chart:
    top_n = st.slider("표시할 키워드 수", 5, 20, 10, key="trend_topn")
    trend_df = keyword_trend(filtered, top_n=top_n)
    if not trend_df.empty:
        # Plotly 라인차트 — 인터랙티브
        fig = px.line(
            trend_df.reset_index(),
            x="month",
            y=trend_df.columns.tolist(),
            markers=True,
            labels={"value": "논문 수", "month": "월", "variable": "키워드"},
        )
        fig.update_layout(
            height=400,
            margin=dict(l=0, r=0, t=10, b=0),
            legend=dict(orientation="h", yanchor="bottom", y=-0.3),
            hovermode="x unified",
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("키워드 트렌드를 생성할 데이터가 부족합니다.")

with col_cloud:
    keywords = extract_keywords(filtered, top_n=50)
    if keywords:
        word_freq = {kw: score for kw, score in keywords}
        wc = WordCloud(
            width=600, height=400,
            background_color="white",
            color_func=lambda *a, **kw: "#002F6C",
            prefer_horizontal=0.7,
            max_words=40,
        ).generate_from_frequencies(word_freq)
        fig_wc, ax = plt.subplots(figsize=(6, 4))
        ax.imshow(wc, interpolation="bilinear")
        ax.axis("off")
        plt.tight_layout(pad=0)
        st.pyplot(fig_wc)
        plt.close()
    else:
        st.info("데이터가 부족합니다.")

# 키워드 상세 (접을 수 있는 expander)
with st.expander("Top 20 키워드 상세 보기"):
    kw_all = extract_keywords(filtered, top_n=20)
    if kw_all:
        kw_df = pd.DataFrame(kw_all, columns=["키워드", "TF-IDF 점수"])
        kw_df.index = range(1, len(kw_df) + 1)
        kw_df.index.name = "순위"
        kw_df["TF-IDF 점수"] = kw_df["TF-IDF 점수"].round(4)
        st.dataframe(kw_df, use_container_width=True)


# --- 섹션 2: 저널 × 키워드 + 저널 통계 ---
st.markdown('<hr>', unsafe_allow_html=True)
st.markdown('<p class="sec-head">저널 분석</p>', unsafe_allow_html=True)

col_heat, col_freq = st.columns([3, 2])

with col_heat:
    top_n_ct = st.slider("히트맵 키워드 수", 5, 20, 10, key="ct_topn")
    ct_df = journal_keyword_crosstab(filtered, top_n_keywords=top_n_ct)
    if not ct_df.empty:
        # Plotly 히트맵 — 호버 툴팁
        fig = px.imshow(
            ct_df,
            aspect="auto",
            color_continuous_scale=[[0, "#F3F4F5"], [0.5, "#AEC6FF"], [1, "#001B44"]],
            labels=dict(x="키워드", y="저널", color="빈도"),
        )
        fig.update_layout(
            height=max(400, len(ct_df) * 28),
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("데이터가 부족합니다.")

with col_freq:
    freq = journal_frequency(filtered).head(12)
    if not freq.empty:
        fig = px.bar(
            x=freq.values,
            y=freq.index,
            orientation="h",
            labels={"x": "논문 수", "y": ""},
            color=freq.values,
            color_continuous_scale=[[0, "#AEC6FF"], [1, "#002F6C"]],
        )
        fig.update_layout(
            height=max(400, len(freq) * 28),
            margin=dict(l=0, r=0, t=10, b=0),
            showlegend=False,
            coloraxis_showscale=False,
        )
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)

# 키워드 → 저널 검색
with st.expander("키워드로 저널 검색"):
    search_kw = st.text_input("키워드를 입력하세요", value="", key="kw_search")
    if search_kw:
        filtered_copy = filtered.copy()
        filtered_copy["clean_title"] = filtered_copy["title"].apply(
            lambda t: remove_stopwords(clean_title(t))
        )
        matched = filtered_copy[
            filtered_copy["clean_title"].str.contains(search_kw.lower(), na=False)
        ]
        if not matched.empty:
            rank = matched["journal"].value_counts()
            fig = px.bar(
                x=rank.values, y=rank.index, orientation="h",
                labels={"x": "논문 수", "y": ""},
                color=rank.values, color_continuous_scale=[[0, "#AEC6FF"], [1, "#002F6C"]],
            )
            fig.update_layout(
                height=max(250, len(rank) * 25),
                margin=dict(l=0, r=0, t=10, b=0),
                showlegend=False, coloraxis_showscale=False,
            )
            fig.update_yaxes(autorange="reversed")
            st.plotly_chart(fig, use_container_width=True)
            st.caption(f"**'{search_kw}'** 포함 논문: {len(matched)}건")
        else:
            st.warning(f"'{search_kw}'를 포함하는 논문이 없습니다.")


# --- 섹션 3: AI 관심 분석 ---
st.markdown('<hr>', unsafe_allow_html=True)
st.markdown('<p class="sec-head">AI 관심 논문 분석</p>', unsafe_allow_html=True)

col_ratio, col_compare = st.columns([3, 2])

with col_ratio:
    ratio_df = interest_ratio_by_journal(filtered)
    top_ratio = ratio_df[ratio_df["interest"] > 0].head(15)
    if not top_ratio.empty:
        fig = px.bar(
            top_ratio,
            x="ratio", y="journal", orientation="h",
            labels={"ratio": "관심 비율", "journal": ""},
            color="ratio",
            color_continuous_scale=[[0, "#AEC6FF"], [1, "#002F6C"]],
            text=top_ratio["ratio"].apply(lambda x: f"{x:.0%}"),
        )
        fig.update_layout(
            height=max(300, len(top_ratio) * 28),
            margin=dict(l=0, r=0, t=10, b=0),
            showlegend=False, coloraxis_showscale=False,
        )
        fig.update_traces(textposition="outside")
        fig.update_yaxes(autorange="reversed")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("AI 관심 논문이 없습니다.")

with col_compare:
    all_kw, int_kw = interest_keywords(filtered, top_n=10)
    if all_kw and int_kw:
        st.markdown("**전체 vs 관심 키워드 비교**")
        max_len = min(10, max(len(all_kw), len(int_kw)))
        compare_data = []
        for i in range(max_len):
            compare_data.append({
                "순위": i + 1,
                "전체 Top 키워드": all_kw[i][0] if i < len(all_kw) else "",
                "관심 논문 키워드": int_kw[i][0] if i < len(int_kw) else "",
            })
        compare_df = pd.DataFrame(compare_data)
        st.dataframe(compare_df, use_container_width=True, hide_index=True)
    elif all_kw:
        st.info("AI 관심 논문이 부족하여 비교할 수 없습니다.")
    else:
        st.info("데이터가 부족합니다.")


# --- 섹션 4: 월별 트렌드 ---
st.markdown('<hr>', unsafe_allow_html=True)
st.markdown('<p class="sec-head">월별 활동</p>', unsafe_allow_html=True)

monthly = journal_monthly_frequency(filtered)
if not monthly.empty:
    monthly_total = monthly.sum(axis=1)
    fig = px.area(
        x=monthly_total.index,
        y=monthly_total.values,
        labels={"x": "월", "y": "논문 수"},
    )
    fig.update_layout(
        height=250,
        margin=dict(l=0, r=0, t=10, b=0),
    )
    fig.update_traces(
        fill="tozeroy",
        line_color="#002F6C",
        fillcolor="rgba(0,47,108,0.08)",
    )
    st.plotly_chart(fig, use_container_width=True)

    with st.expander("저널별 월별 상세"):
        st.dataframe(monthly, use_container_width=True)


# --- 리포트 다운로드 ---
st.markdown('<hr>', unsafe_allow_html=True)
col_dl, _ = st.columns([1, 3])
with col_dl:
    if st.button("📄 리포트 생성 및 다운로드", use_container_width=True):
        md = generate_report(filtered, start=start_month, end=end_month)
        save_report(md, start_month, end_month)
        st.download_button(
            label="다운로드 (.md)",
            data=md,
            file_name=f"{start_month}_to_{end_month}_analysis.md",
            mime="text/markdown",
            use_container_width=True,
        )
