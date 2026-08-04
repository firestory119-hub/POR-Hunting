import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

st.set_page_config(page_title="Alpha Radar", page_icon="⭐", layout="wide")

DATA_DIR = "data"
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
MARKET_DATA_CSV = os.path.join(DATA_DIR, "market_data.csv")
MARKET_HISTORY_CSV = os.path.join(DATA_DIR, "market_history.csv")
FINANCIAL_DATA_CSV = os.path.join(DATA_DIR, "financial_data.csv")
CONSENSUS_XLSX = os.path.join(DATA_DIR, "consensus.xlsx")


def clean_ticker(value):
    text = str(value or "").strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def load_favorites():
    key = "_session_favorites.csv"
    if key in st.session_state:
        df = st.session_state[key].copy()
    elif os.path.exists(FAVORITES_FILE):
        df = pd.read_csv(FAVORITES_FILE, dtype=str)
    else:
        return pd.DataFrame(columns=["name", "ticker", "saved_at"])

    for col in ["name", "ticker", "saved_at"]:
        if col not in df.columns:
            df[col] = ""

    df["ticker"] = df["ticker"].map(clean_ticker)
    return df[["name", "ticker", "saved_at"]].drop_duplicates(
        "ticker", keep="last"
    )


@st.cache_data(show_spinner=False, ttl=300)
def load_market():
    if not os.path.exists(MARKET_DATA_CSV):
        return pd.DataFrame()

    df = pd.read_csv(MARKET_DATA_CSV, dtype=str)
    rename = {}
    if "name" in df.columns and "종목명" not in df.columns:
        rename["name"] = "종목명"
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename["ticker"] = "종목코드"
    if "price" in df.columns and "현재가" not in df.columns:
        rename["price"] = "현재가"
    if "market_cap_eok" in df.columns and "현재시총_억원" not in df.columns:
        rename["market_cap_eok"] = "현재시총_억원"
    df = df.rename(columns=rename)

    if not {"종목명", "종목코드"}.issubset(df.columns):
        return pd.DataFrame()

    df["종목코드"] = df["종목코드"].map(clean_ticker)
    for col in ["현재가", "현재시총_억원"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return df.drop_duplicates("종목코드")


@st.cache_data(show_spinner=False, ttl=300)
def load_financials():
    if not os.path.exists(FINANCIAL_DATA_CSV):
        return pd.DataFrame()

    df = pd.read_csv(FINANCIAL_DATA_CSV, dtype={"ticker": str})
    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})
    if "ticker" not in df.columns or "year" not in df.columns:
        return pd.DataFrame()

    df["ticker"] = df["ticker"].map(clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["operating_income"] = pd.to_numeric(
        df.get("operating_income"), errors="coerce"
    )
    return df


@st.cache_data(show_spinner=False, ttl=300)
def load_consensus():
    columns = [
        "name", "ticker", "year", "operating_income_eok",
        "target_por", "updated_at"
    ]
    if not os.path.exists(CONSENSUS_XLSX):
        return pd.DataFrame(columns=columns)

    try:
        wide = pd.read_excel(
            CONSENSUS_XLSX,
            sheet_name="컨센서스입력",
            header=1,
            dtype={"종목코드": str},
            engine="openpyxl",
        )
    except Exception:
        return pd.DataFrame(columns=columns)

    if not {"종목명", "종목코드"}.issubset(wide.columns):
        return pd.DataFrame(columns=columns)

    year_cols = [
        c for c in wide.columns
        if re.fullmatch(r"\d{4}E?", str(c).strip())
    ]
    id_cols = [
        c for c in ["종목명", "종목코드", "목표POR", "업데이트일"]
        if c in wide.columns
    ]

    long_df = wide.melt(
        id_vars=id_cols,
        value_vars=year_cols,
        var_name="year",
        value_name="operating_income_eok",
    ).rename(columns={
        "종목명": "name",
        "종목코드": "ticker",
        "목표POR": "target_por",
        "업데이트일": "updated_at",
    })

    long_df["ticker"] = long_df["ticker"].map(clean_ticker)
    long_df["year"] = pd.to_numeric(
        long_df["year"].astype(str).str.extract(r"(\d{4})")[0],
        errors="coerce",
    )
    long_df["operating_income_eok"] = pd.to_numeric(
        long_df["operating_income_eok"], errors="coerce"
    )
    if "target_por" not in long_df.columns:
        long_df["target_por"] = None
    if "updated_at" not in long_df.columns:
        long_df["updated_at"] = None
    long_df["target_por"] = pd.to_numeric(
        long_df["target_por"], errors="coerce"
    )

    return long_df.dropna(
        subset=["ticker", "year", "operating_income_eok"]
    )


@st.cache_data(show_spinner=False, ttl=300)
def load_history(tickers):
    cols = ["ticker", "date", "price", "market_cap"]
    if not os.path.exists(MARKET_HISTORY_CSV):
        return pd.DataFrame(columns=cols)

    try:
        df = pd.read_csv(
            MARKET_HISTORY_CSV,
            usecols=cols,
            dtype={"ticker": str},
        )
    except Exception:
        return pd.DataFrame(columns=cols)

    df["ticker"] = df["ticker"].map(clean_ticker)
    df = df[df["ticker"].isin(tickers)].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")
    return df.dropna(subset=["ticker", "date", "market_cap"])


def latest_positive(financials, ticker):
    rows = financials[
        (financials["ticker"] == ticker)
        & financials["operating_income"].notna()
        & (financials["operating_income"] > 0)
    ].sort_values("year")

    if rows.empty:
        return None, None

    row = rows.iloc[-1]
    return int(row["year"]), float(row["operating_income"]) / 100_000_000


def average_por(history, financials, ticker, years):
    stock = history[history["ticker"] == ticker].sort_values("date")
    actual = financials[
        (financials["ticker"] == ticker)
        & financials["operating_income"].notna()
        & (financials["operating_income"] > 0)
    ][["year", "operating_income"]]

    if stock.empty or actual.empty:
        return None

    cutoff = stock["date"].max() - pd.DateOffset(years=years)
    stock = stock[stock["date"] >= cutoff]
    actual_map = {
        int(r["year"]): float(r["operating_income"])
        for _, r in actual.iterrows()
    }

    values = []
    for _, r in stock.iterrows():
        candidates = [y for y in actual_map if y <= int(r["date"].year)]
        if not candidates:
            continue
        base = actual_map[max(candidates)]
        if base > 0 and r["market_cap"] > 0:
            values.append(r["market_cap"] / base)

    return float(pd.Series(values).mean()) if values else None


def alpha_score(current_por, avg_por, expected_por, upside, growth):
    score = 0.0
    if current_por and avg_por and avg_por > 0:
        discount = (avg_por - current_por) / avg_por
        score += max(0, min(35, 17.5 + discount * 70))
    if expected_por:
        score += max(0, min(25, (15 - expected_por) * 2.5))
    if upside is not None:
        score += max(0, min(25, upside / 4))
    if growth is not None:
        score += max(0, min(15, growth / 6))
    return round(max(0, min(100, score)), 1)


def stars(score):
    n = max(1, min(5, int((score + 19.999) // 20)))
    return "★" * n + "☆" * (5 - n)


def signal(score):
    if score >= 85:
        return "🟢 Strong Buy"
    if score >= 70:
        return "🟢 Buy"
    if score >= 55:
        return "🟡 Watch"
    if score >= 40:
        return "🟠 Neutral"
    return "🔴 Caution"


def score_badge(score):
    if score >= 85:
        return "#d8f3dc", "#1b4332"
    if score >= 70:
        return "#e9f5db", "#386641"
    if score >= 55:
        return "#fff3bf", "#7f4f24"
    if score >= 40:
        return "#ffe8cc", "#9c2c13"
    return "#ffe3e3", "#c92a2a"


def signed_badge(value):
    if value is None or pd.isna(value):
        return "#f1f3f5", "#495057"
    if value >= 50:
        return "#d8f3dc", "#1b4332"
    if value >= 0:
        return "#e9f5db", "#386641"
    return "#ffe3e3", "#c92a2a"


def build_radar(
    favorites, market, financials, consensus, history,
    selected_year, average_years, default_target_por
):
    rows = []

    for _, fav in favorites.iterrows():
        ticker = clean_ticker(fav["ticker"])
        m = market[market["종목코드"] == ticker]
        if m.empty:
            continue

        m = m.iloc[0]
        name = str(m.get("종목명", fav.get("name", ticker)))
        h = history[history["ticker"] == ticker].sort_values("date")

        if not h.empty:
            last = h.iloc[-1]
            price = float(last["price"])
            mcap = float(last["market_cap"]) / 100_000_000
            base_date = last["date"]
        else:
            price = pd.to_numeric(m.get("현재가"), errors="coerce")
            mcap = pd.to_numeric(m.get("현재시총_억원"), errors="coerce")
            base_date = pd.NaT

        profit_year, actual_oi = latest_positive(financials, ticker)
        current_por = (
            mcap / actual_oi
            if pd.notna(mcap) and mcap > 0 and actual_oi and actual_oi > 0
            else None
        )
        avg_por = average_por(
            history, financials, ticker, average_years
        )

        c = consensus[
            (consensus["ticker"] == ticker)
            & (consensus["year"] == selected_year)
        ]
        expected_oi = None
        target_por = float(default_target_por)
        updated_at = None

        if not c.empty:
            c = c.iloc[-1]
            expected_oi = float(c["operating_income_eok"])
            saved_target = pd.to_numeric(c.get("target_por"), errors="coerce")
            if pd.notna(saved_target) and saved_target > 0:
                target_por = float(saved_target)
            updated_at = c.get("updated_at")

        expected_por = None
        target_price = None
        upside = None
        growth = None

        if expected_oi and expected_oi > 0 and pd.notna(mcap) and mcap > 0:
            expected_por = mcap / expected_oi
            target_mcap = expected_oi * target_por
            if pd.notna(price) and price > 0:
                target_price = price * target_mcap / mcap
                upside = (target_price / price - 1) * 100

        if expected_oi and actual_oi and actual_oi > 0:
            growth = (expected_oi / actual_oi - 1) * 100

        discount_rate = None
        if (
            current_por is not None
            and avg_por is not None
            and avg_por > 0
        ):
            discount_rate = (
                (avg_por - current_por) / avg_por * 100
            )

        score = alpha_score(
            current_por, avg_por, expected_por, upside, growth
        )

        reasons = []

        if discount_rate is not None:
            if discount_rate >= 50:
                reasons.append(
                    f"평균 POR 대비 {discount_rate:.1f}% 할인"
                )
            elif discount_rate > 0:
                reasons.append(
                    f"평균 POR 대비 {discount_rate:.1f}% 저평가"
                )

        if expected_por is not None:
            reasons.append(
                f"{selected_year}E POR {expected_por:.2f}배"
            )

        if growth is not None and growth > 0:
            reasons.append(
                f"영업이익 성장률 +{growth:.1f}%"
            )

        if upside is not None:
            reasons.append(
                f"목표가 상승여력 {upside:+.1f}%"
            )

        rows.append({
            "종목명": name,
            "종목코드": ticker,
            "기준일": base_date,
            "현재가": price,
            "현재시총(억)": mcap,
            "최근 흑자연도": profit_year,
            "최근 흑자 영업이익(억)": actual_oi,
            "최근 흑자 기준 POR": current_por,
            f"{average_years}년 평균 POR": avg_por,
            "평균 대비 할인율(%)": discount_rate,
            f"{selected_year}E 영업이익(억)": expected_oi,
            f"{selected_year}E POR": expected_por,
            "목표 POR": target_por,
            "목표 주가": target_price,
            "상승여력(%)": upside,
            "영업이익 성장률(%)": growth,
            "Alpha Score": score,
            "Alpha": stars(score),
            "Signal": signal(score),
            "컨센서스 수정일": updated_at,
            "추천 이유": " · ".join(reasons[:4]),
        })

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values(
        ["Alpha Score", "상승여력(%)"],
        ascending=[False, False],
        na_position="last",
    ).reset_index(drop=True)
    df.insert(0, "순위", range(1, len(df) + 1))
    return df


st.title("⭐ Alpha Radar V50.3")
st.caption(
    "즐겨찾기 전체를 현재 POR, 장기 평균 POR, 할인율, "
    "상승여력과 투자 의견으로 자동 순위화합니다."
)

favorites = load_favorites()
market = load_market()
financials = load_financials()
consensus = load_consensus()

if favorites.empty:
    st.warning("즐겨찾기가 없습니다.")
    st.stop()

if market.empty or financials.empty:
    st.error("market_data.csv 또는 financial_data.csv를 읽지 못했습니다.")
    st.stop()

history = load_history(tuple(favorites["ticker"].tolist()))
available_years = sorted(
    consensus["year"].dropna().astype(int).unique().tolist()
) or [datetime.today().year]

c1, c2, c3, c4 = st.columns(4)
with c1:
    selected_year = st.selectbox("컨센서스 연도", available_years)
with c2:
    average_years = st.selectbox(
        "평균 POR 기간",
        [3, 5, 10],
        index=2,
        format_func=lambda x: f"{x}년",
    )
with c3:
    default_target_por = st.number_input(
        "기본 목표 POR",
        min_value=1.0,
        max_value=50.0,
        value=8.0,
        step=0.5,
    )
with c4:
    minimum_score = st.slider(
        "최소 Alpha Score",
        0,
        100,
        0,
        5,
    )

st.markdown("### 빠른 필터")
f1, f2, f3, f4, f5 = st.columns(5)

with f1:
    minimum_stars = st.selectbox(
        "최소 별점",
        ["전체", "★★★☆☆ 이상", "★★★★☆ 이상", "★★★★★만"],
        index=0,
    )

with f2:
    max_expected_por = st.number_input(
        f"최대 {selected_year}E POR",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=0.5,
        help="0이면 제한 없음",
    )

with f3:
    min_discount = st.number_input(
        "최소 할인율(%)",
        min_value=0.0,
        max_value=100.0,
        value=0.0,
        step=5.0,
        help="0이면 제한 없음",
    )

with f4:
    min_upside = st.number_input(
        "최소 상승여력(%)",
        min_value=-100.0,
        max_value=1000.0,
        value=-100.0,
        step=10.0,
    )

with f5:
    consensus_only = st.checkbox(
        "컨센서스 있는 종목만",
        value=False,
    )

with st.spinner(f"즐겨찾기 {len(favorites)}개 분석 중..."):
    radar = build_radar(
        favorites,
        market,
        financials,
        consensus,
        history,
        int(selected_year),
        int(average_years),
        float(default_target_por),
    )

if radar.empty:
    st.warning("계산 가능한 종목이 없습니다.")
    st.stop()

filtered = radar[
    radar["Alpha Score"] >= minimum_score
].copy()

star_threshold_map = {
    "전체": 0,
    "★★★☆☆ 이상": 3,
    "★★★★☆ 이상": 4,
    "★★★★★만": 5,
}

star_threshold = star_threshold_map[minimum_stars]

if star_threshold > 0:
    filtered = filtered[
        filtered["Alpha"].str.count("★") >= star_threshold
    ]

if max_expected_por > 0:
    filtered = filtered[
        pd.to_numeric(
            filtered[f"{selected_year}E POR"],
            errors="coerce",
        ) <= max_expected_por
    ]

if min_discount > 0:
    filtered = filtered[
        pd.to_numeric(
            filtered["평균 대비 할인율(%)"],
            errors="coerce",
        ) >= min_discount
    ]

filtered = filtered[
    pd.to_numeric(
        filtered["상승여력(%)"],
        errors="coerce",
    ).fillna(-9999) >= min_upside
]

if consensus_only:
    filtered = filtered[
        filtered[
            f"{selected_year}E 영업이익(억)"
        ].notna()
    ]

sort_col = st.radio(
    "정렬 기준",
    [
        "Alpha Score",
        f"{selected_year}E POR",
        "최근 흑자 기준 POR",
        "평균 대비 할인율(%)",
        "상승여력(%)",
        "영업이익 성장률(%)",
    ],
    horizontal=True,
)
ascending = sort_col in [
    f"{selected_year}E POR",
    "최근 흑자 기준 POR",
]
filtered = filtered.sort_values(
    sort_col,
    ascending=ascending,
    na_position="last",
).reset_index(drop=True)

if filtered.empty:
    st.warning("현재 필터 조건에 맞는 종목이 없습니다.")
    st.stop()

filtered["순위"] = range(1, len(filtered) + 1)

m1, m2, m3, m4 = st.columns(4)
m1.metric("즐겨찾기", f"{len(radar)}개")
m2.metric("평균 Alpha", f"{radar['Alpha Score'].mean():.1f}점")
m3.metric(
    "Strong/Buy",
    f"{radar['Signal'].isin(['🟢 Strong Buy', '🟢 Buy']).sum()}개",
)
m4.metric(
    "컨센서스 보유",
    f"{radar[f'{selected_year}E 영업이익(억)'].notna().sum()}개",
)

st.markdown("### Alpha TOP 10")
top10 = filtered.head(10)

fig = go.Figure(
    go.Bar(
        x=top10["Alpha Score"],
        y=top10["종목명"],
        orientation="h",
        text=top10.apply(
            lambda r: f"{r['Alpha Score']:.1f}점 {r['Alpha']}",
            axis=1,
        ),
        textposition="auto",
        customdata=top10[
            [
                "평균 대비 할인율(%)",
                "상승여력(%)",
                f"{selected_year}E POR",
                "Signal",
            ]
        ],
        hovertemplate=(
            "<b>%{y}</b><br>"
            "Alpha Score: %{x:.1f}점<br>"
            "할인율: %{customdata[0]:.1f}%<br>"
            "상승여력: %{customdata[1]:.1f}%<br>"
            f"{selected_year}E POR: "
            "%{customdata[2]:.2f}배<br>"
            "%{customdata[3]}"
            "<extra></extra>"
        ),
    )
)
fig.update_layout(
    height=max(420, len(top10) * 48),
    xaxis_title="Alpha Score",
    yaxis=dict(autorange="reversed"),
    margin=dict(l=30, r=30, t=30, b=40),
)
st.plotly_chart(fig, use_container_width=True)

st.markdown("### 오늘의 추천 TOP 5")
recommendations = filtered.head(5)
card_columns = st.columns(min(5, len(recommendations)))

for index, (_, row) in enumerate(recommendations.iterrows()):
    with card_columns[index]:
        score_bg, score_fg = score_badge(row["Alpha Score"])
        upside_bg, upside_fg = signed_badge(row.get("상승여력(%)"))
        discount_bg, discount_fg = signed_badge(
            row.get("평균 대비 할인율(%)")
        )

        discount_text = (
            f"{row['평균 대비 할인율(%)']:.1f}%"
            if pd.notna(row.get("평균 대비 할인율(%)"))
            else "-"
        )
        upside_text = (
            f"{row['상승여력(%)']:+.1f}%"
            if pd.notna(row.get("상승여력(%)"))
            else "-"
        )
        expected_por_text = (
            f"{row[f'{selected_year}E POR']:.2f}배"
            if pd.notna(row.get(f"{selected_year}E POR"))
            else "-"
        )

        st.markdown(
            f"""
            <div style="
                border:1px solid #dee2e6;
                border-radius:14px;
                padding:16px;
                min-height:285px;
                background:white;
                box-shadow:0 2px 8px rgba(0,0,0,0.05);
            ">
                <div style="font-size:20px;font-weight:800;margin-bottom:6px;">
                    {row['종목명']}
                </div>
                <div style="font-size:18px;margin-bottom:10px;">
                    {row['Alpha']}
                </div>
                <div style="
                    display:inline-block;
                    background:{score_bg};
                    color:{score_fg};
                    border-radius:999px;
                    padding:5px 10px;
                    font-weight:700;
                    margin-bottom:12px;
                ">
                    {row['Signal']} · {row['Alpha Score']:.1f}점
                </div>
                <div style="margin:8px 0;">
                    예상 POR <b>{expected_por_text}</b>
                </div>
                <div style="margin:8px 0;">
                    할인율
                    <span style="
                        background:{discount_bg};
                        color:{discount_fg};
                        border-radius:8px;
                        padding:3px 7px;
                        font-weight:700;
                    ">{discount_text}</span>
                </div>
                <div style="margin:8px 0;">
                    상승여력
                    <span style="
                        background:{upside_bg};
                        color:{upside_fg};
                        border-radius:8px;
                        padding:3px 7px;
                        font-weight:700;
                    ">{upside_text}</span>
                </div>
                <div style="
                    color:#6c757d;
                    font-size:12px;
                    line-height:1.45;
                    margin-top:12px;
                ">
                    {row.get('추천 이유', '')}
                </div>
            </div>
            """,
            unsafe_allow_html=True,
        )

st.markdown("### 전체 순위")
st.caption(
    "투자 의견 기준: 85점 이상 Strong Buy · 70점 이상 Buy · "
    "55점 이상 Watch · 40점 이상 Neutral · 그 미만 Caution"
)

columns = [
    "순위",
    "종목명",
    "Alpha",
    "Signal",
    "Alpha Score",
    "현재가",
    "현재시총(억)",
    "최근 흑자연도",
    "최근 흑자 기준 POR",
    f"{average_years}년 평균 POR",
    "평균 대비 할인율(%)",
    f"{selected_year}E 영업이익(억)",
    f"{selected_year}E POR",
    "목표 POR",
    "목표 주가",
    "상승여력(%)",
    "영업이익 성장률(%)",
    "추천 이유",
    "컨센서스 수정일",
]

st.dataframe(
    filtered[columns],
    use_container_width=True,
    hide_index=True,
    column_config={
        "Alpha Score": st.column_config.ProgressColumn(
            min_value=0,
            max_value=100,
            format="%.1f",
        ),
        "현재가": st.column_config.NumberColumn(format="%,.0f원"),
        "현재시총(억)": st.column_config.NumberColumn(format="%,.0f억"),
        "최근 흑자 기준 POR": st.column_config.NumberColumn(format="%.2f배"),
        f"{average_years}년 평균 POR": st.column_config.NumberColumn(
            format="%.2f배"
        ),
        f"{selected_year}E 영업이익(억)": st.column_config.NumberColumn(
            format="%,.1f억"
        ),
        "평균 대비 할인율(%)": st.column_config.NumberColumn(
            format="%.1f%%"
        ),
        f"{selected_year}E POR": st.column_config.NumberColumn(
            format="%.2f배"
        ),
        "목표 POR": st.column_config.NumberColumn(format="%.1f배"),
        "목표 주가": st.column_config.NumberColumn(format="%,.0f원"),
        "상승여력(%)": st.column_config.NumberColumn(format="%.1f%%"),
        "영업이익 성장률(%)": st.column_config.NumberColumn(format="%.1f%%"),
    },
)

st.markdown("### 종목분석으로 이동")
move_col1, move_col2 = st.columns([3, 1])

with move_col1:
    stock_name = st.selectbox(
        "분석할 종목",
        filtered["종목명"].tolist(),
    )

with move_col2:
    st.write("")
    st.write("")
    open_stock = st.button(
        "📈 차트 열기",
        type="primary",
        use_container_width=True,
    )

if open_stock:
    st.session_state["stock_query"] = stock_name
    st.query_params["collecting_name"] = stock_name
    try:
        st.switch_page("app.py")
    except Exception:
        st.success(f"종목분석에서 '{stock_name}'을 검색하세요.")

st.download_button(
    "📥 Alpha Radar CSV 다운로드",
    data=filtered.to_csv(index=False, encoding="utf-8-sig"),
    file_name=f"alpha_radar_{selected_year}E.csv",
    mime="text/csv",
)

with st.expander("Alpha Score 계산 방식"):
    st.markdown(
        """
- 장기 평균 POR 대비 할인 정도: 최대 35점
- 예상 POR의 낮은 정도: 최대 25점
- 목표 POR 기준 상승여력: 최대 25점
- 최근 흑자 대비 예상 영업이익 성장률: 최대 15점
        """
    )
