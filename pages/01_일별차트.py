import csv
import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(page_title="일별 차트", layout="wide")

DATA_DIR = Path("data")
MARKET_FILE = DATA_DIR / "market_data.csv"
FINANCIAL_FILE = DATA_DIR / "financial_data.csv"
HISTORY_FILE = DATA_DIR / "market_history.csv"

st.title("일별 POR / PER / PBR 차트")
st.caption("기존 메인 앱과 분리된 독립 페이지 · 선택한 종목의 일별 데이터만 읽습니다.")


def clean_ticker(value) -> str:
    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def clean_num(value):
    if value is None:
        return None
    text = str(value).replace(",", "").strip()
    if text in {"", "-", "None", "nan"}:
        return None
    try:
        return float(text)
    except Exception:
        return None


@st.cache_data(show_spinner=False, ttl=3600)
def load_market() -> pd.DataFrame:
    if not MARKET_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(MARKET_FILE, dtype=str)

    rename = {}
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename["ticker"] = "종목코드"
    if "name" in df.columns and "종목명" not in df.columns:
        rename["name"] = "종목명"
    if rename:
        df = df.rename(columns=rename)

    if "종목코드" not in df.columns or "종목명" not in df.columns:
        return pd.DataFrame()

    df["종목코드"] = df["종목코드"].map(clean_ticker)
    return (
        df[["종목코드", "종목명"]]
        .dropna()
        .drop_duplicates("종목코드")
        .sort_values("종목명")
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False, ttl=3600)
def load_financial(ticker: str) -> pd.DataFrame:
    if not FINANCIAL_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(FINANCIAL_FILE, dtype=str)

    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})
    if "종목명" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"종목명": "name"})

    if "ticker" not in df.columns or "year" not in df.columns:
        return pd.DataFrame()

    df["ticker"] = df["ticker"].map(clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for col in ["operating_income", "net_income", "equity"]:
        if col not in df.columns:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")

    return (
        df[df["ticker"] == ticker]
        .dropna(subset=["year"])
        .sort_values("year")
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False, ttl=1800)
def load_daily_history(ticker: str) -> pd.DataFrame:
    """
    market_history.csv 전체를 pandas로 읽지 않고,
    선택한 종목 행만 한 줄씩 읽어 메모리 사용을 줄입니다.
    """
    columns = ["date", "price", "market_cap"]

    if not HISTORY_FILE.exists():
        return pd.DataFrame(columns=columns)

    rows = []

    with open(HISTORY_FILE, "r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        required = {"ticker", "date", "price", "market_cap"}
        if not required.issubset(set(reader.fieldnames or [])):
            return pd.DataFrame(columns=columns)

        for row in reader:
            if clean_ticker(row.get("ticker")) != ticker:
                continue

            date_value = pd.to_datetime(row.get("date"), errors="coerce")
            price_value = clean_num(row.get("price"))
            market_cap_value = clean_num(row.get("market_cap"))

            if pd.isna(date_value) or market_cap_value is None:
                continue

            rows.append(
                {
                    "date": date_value,
                    "price": price_value,
                    "market_cap": market_cap_value,
                }
            )

    if not rows:
        return pd.DataFrame(columns=columns)

    return (
        pd.DataFrame(rows)
        .drop_duplicates("date", keep="last")
        .sort_values("date")
        .reset_index(drop=True)
    )


market = load_market()

if market.empty:
    st.error("data/market_data.csv를 읽을 수 없습니다.")
    st.stop()

query = st.text_input("종목명 또는 종목코드", value="").strip()

if query:
    found = market[
        market["종목명"].astype(str).str.contains(query, case=False, na=False)
        | market["종목코드"].astype(str).str.contains(query, na=False)
    ].head(100)
else:
    found = market.head(100)

if found.empty:
    st.warning("검색 결과가 없습니다.")
    st.stop()

label_map = {
    f"{row['종목명']} ({row['종목코드']})": row["종목코드"]
    for _, row in found.iterrows()
}

choice = st.selectbox("종목 선택", list(label_map.keys()))
ticker = label_map[choice]
name = choice.rsplit(" (", 1)[0]

c1, c2, c3 = st.columns(3)

with c1:
    metric = st.radio("지표", ["POR", "PER", "PBR"], horizontal=True)

with c2:
    period = st.radio("기간", ["1년", "3년", "5년", "10년", "전체"], horizontal=True)

with c3:
    load_button = st.button("일별 차트 불러오기", type="primary", width="stretch")

if not load_button:
    st.info("종목과 지표를 선택한 뒤 **일별 차트 불러오기**를 누르세요.")
    st.stop()

with st.spinner("선택 종목의 일별 데이터를 읽는 중..."):
    daily = load_daily_history(ticker)
    financial = load_financial(ticker)

if daily.empty:
    st.warning(
        f"{name}의 일별 데이터가 없습니다. "
        "update_market_daily.py 수집 대상에 포함되어 있는지 확인하세요."
    )
    st.stop()

if financial.empty:
    st.warning(f"{name}의 재무 데이터가 없습니다.")
    st.stop()

metric_col = {
    "POR": "operating_income",
    "PER": "net_income",
    "PBR": "equity",
}[metric]

financial = financial.dropna(subset=[metric_col]).copy()
financial = financial[financial[metric_col] > 0]

if financial.empty:
    st.warning(f"{metric} 계산에 사용할 양수 재무 데이터가 없습니다.")
    st.stop()

daily["year"] = pd.to_numeric(
    daily["date"].dt.year, errors="coerce"
).astype("Int64")

# 각 일자에 사용할 가장 최근 연간 재무를 연결
basis = financial[["year", metric_col]].copy()
basis["year"] = pd.to_numeric(basis["year"], errors="coerce").astype("Int64")
basis = (
    basis.dropna(subset=["year", metric_col])
    .drop_duplicates("year", keep="last")
    .sort_values("year")
)

daily = daily.dropna(subset=["year"]).copy()
daily["year"] = daily["year"].astype("int64")
basis["year"] = basis["year"].astype("int64")

daily = pd.merge_asof(
    daily.sort_values(["year", "date"]),
    basis.sort_values("year"),
    on="year",
    direction="backward",
)

daily = daily.dropna(subset=[metric_col]).copy()
daily["multiple"] = daily["market_cap"] / daily[metric_col]
daily = daily[(daily["multiple"] > 0) & (daily["multiple"] < 300)]

if period != "전체":
    years = int(period.replace("년", ""))
    cutoff = daily["date"].max() - pd.DateOffset(years=years)
    daily = daily[daily["date"] >= cutoff]

if daily.empty:
    st.warning("선택한 기간에 표시할 데이터가 없습니다.")
    st.stop()

mean = daily["multiple"].mean()
std = daily["multiple"].std(ddof=0)
current = daily.iloc[-1]

st.subheader(f"{name} ({ticker}) · 일별 {metric}")

m1, m2, m3, m4 = st.columns(4)
m1.metric("현재가", f"{current['price']:,.0f}원" if pd.notna(current["price"]) else "-")
m2.metric("현재 시가총액", f"{current['market_cap'] / 100_000_000:,.0f}억")
m3.metric(f"현재 {metric}", f"{current['multiple']:.2f}배")
m4.metric(f"{period} 평균", f"{mean:.2f}배")

fig = go.Figure()

fig.add_trace(
    go.Scatter(
        x=daily["date"],
        y=daily["multiple"],
        mode="lines",
        name=metric,
        customdata=list(
            zip(
                daily["price"],
                daily["market_cap"] / 100_000_000,
                daily[metric_col] / 100_000_000,
            )
        ),
        hovertemplate=(
            "<b>%{x|%Y-%m-%d}</b><br>"
            f"{metric}: " + "%{y:.2f}배<br>"
            "주가: %{customdata[0]:,.0f}원<br>"
            "시가총액: %{customdata[1]:,.0f}억<br>"
            "재무 기준값: %{customdata[2]:,.0f}억"
            "<extra></extra>"
        ),
    )
)

for label, value in [
    ("평균", mean),
    ("+1σ", mean + std),
    ("-1σ", mean - std),
]:
    if pd.notna(value) and value > 0:
        fig.add_hline(
            y=value,
            line_dash="dash",
            annotation_text=f"{label} {value:.2f}",
            annotation_position="right",
        )

fig.update_layout(
    height=620,
    xaxis_title="날짜",
    yaxis_title=f"{metric}(배)",
    hovermode="x unified",
)

st.plotly_chart(fig, width="stretch")

with st.expander("최근 데이터 보기"):
    show = daily[["date", "price", "market_cap", "multiple"]].tail(100).copy()
    show["시가총액(억)"] = show["market_cap"] / 100_000_000
    show = show.rename(
        columns={
            "date": "날짜",
            "price": "주가",
            "multiple": metric,
        }
    )
    st.dataframe(
        show[["날짜", "주가", "시가총액(억)", metric]],
        width="stretch",
        hide_index=True,
    )

st.caption(
    "일별 시가총액은 market_history.csv, "
    "재무 기준값은 financial_data.csv를 사용합니다."
)
