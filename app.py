from pathlib import Path
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(page_title="POR Hunting Pro Stable", layout="wide")

st.title("POR Hunting Pro Stable")
st.caption("CSV 전용 안정판 · 외부 API/pykrx/FinanceDataReader 미사용")


def first_existing(*paths: str) -> Path | None:
    for p in paths:
        path = Path(p)
        if path.exists():
            return path
    return None


MARKET_PATH = first_existing("data/market_data.csv", "market_data.csv")
FIN_PATH = first_existing("data/financial_data.csv", "financial_data.csv")


@st.cache_data(show_spinner=False)
def load_market(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str)

    rename = {
        "ticker": "종목코드",
        "name": "종목명",
        "price": "현재가",
        "market_cap_eok": "현재시총_억원",
        "dividend_yield": "배당수익률",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    required = ["종목코드", "종목명"]
    for c in required:
        if c not in df.columns:
            raise RuntimeError(f"market_data.csv에 '{c}' 열이 없습니다.")

    df["종목코드"] = (
        df["종목코드"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )

    for c in ["현재가", "현재시총_억원", "PER", "PBR", "배당수익률", "상장주식수"]:
        if c in df.columns:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.drop_duplicates("종목코드").reset_index(drop=True)


@st.cache_data(show_spinner=False)
def load_financial(path: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype={"ticker": str})

    rename = {
        "종목코드": "ticker",
        "종목명": "name",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})

    if "ticker" not in df.columns or "year" not in df.columns:
        raise RuntimeError("financial_data.csv에 ticker/year 열이 없습니다.")

    df["ticker"] = (
        df["ticker"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for c in ["revenue", "operating_income", "net_income", "equity", "operating_margin"]:
        if c not in df.columns:
            df[c] = pd.NA
        df[c] = pd.to_numeric(df[c], errors="coerce")

    return df.dropna(subset=["year"]).sort_values(["ticker", "year"])


if MARKET_PATH is None:
    st.error("market_data.csv를 찾지 못했습니다.")
    st.stop()

if FIN_PATH is None:
    st.error("financial_data.csv를 찾지 못했습니다.")
    st.stop()

try:
    market = load_market(str(MARKET_PATH))
    financial = load_financial(str(FIN_PATH))
except Exception as e:
    st.error(str(e))
    st.stop()


query = st.text_input("종목명 또는 종목코드", value="삼성전자").strip()

found = market[
    market["종목명"].astype(str).str.contains(query, case=False, na=False)
    | market["종목코드"].astype(str).str.contains(query, na=False)
].head(50)

if found.empty:
    st.warning("검색 결과가 없습니다.")
    st.stop()

label_map = {
    f"{r['종목명']} ({r['종목코드']})": r["종목코드"]
    for _, r in found.iterrows()
}
choice = st.selectbox("검색 결과", list(label_map.keys()))
ticker = label_map[choice]

m = market[market["종목코드"] == ticker].iloc[0]
name = str(m["종목명"])
fin = financial[financial["ticker"] == ticker].copy()

if fin.empty:
    st.error(f"{name}의 재무 데이터가 없습니다.")
    st.stop()

metric = st.radio("지표", ["POR", "PER", "PBR"], horizontal=True)

base_col = {
    "POR": "operating_income",
    "PER": "net_income",
    "PBR": "equity",
}[metric]

valid_fin = fin.dropna(subset=[base_col])
valid_fin = valid_fin[valid_fin[base_col] > 0]

if valid_fin.empty:
    st.error(f"{metric} 계산에 사용할 양수 기준값이 없습니다.")
    st.stop()

latest_fin = valid_fin.iloc[-1]
current_mcap_eok = float(m.get("현재시총_억원")) if pd.notna(m.get("현재시총_억원")) else None
current_price = float(m.get("현재가")) if pd.notna(m.get("현재가")) else None

if not current_mcap_eok or current_mcap_eok <= 0:
    st.error("현재 시가총액 데이터가 없습니다.")
    st.stop()

base_eok = float(latest_fin[base_col]) / 100_000_000
current_multiple = current_mcap_eok / base_eok

st.subheader(f"{name} ({ticker})")

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("현재가", f"{current_price:,.0f}원" if current_price else "-")
c2.metric("현재 시가총액", f"{current_mcap_eok:,.0f}억")
c3.metric(f"현재 {metric}", f"{current_multiple:.2f}배")
c4.metric("적용 연도", f"{int(latest_fin['year'])}년")
c5.metric("적용 기준값", f"{base_eok:,.1f}억")

st.markdown("### 연도별 재무")

show = fin.copy()
show["매출액(억)"] = show["revenue"] / 100_000_000
show["영업이익(억)"] = show["operating_income"] / 100_000_000
show["당기순이익(억)"] = show["net_income"] / 100_000_000
show["자본총계(억)"] = show["equity"] / 100_000_000
show["영업이익률(%)"] = show["operating_margin"]

cols = [
    "year", "매출액(억)", "영업이익(억)",
    "당기순이익(억)", "자본총계(억)", "영업이익률(%)"
]
st.dataframe(
    show[cols].rename(columns={"year": "연도"}).round(1),
    width="stretch",
    hide_index=True,
)

fig = go.Figure()
fig.add_trace(go.Bar(
    x=show["year"],
    y=show[base_col] / 100_000_000,
    name={"POR": "영업이익", "PER": "당기순이익", "PBR": "자본총계"}[metric],
))
fig.update_layout(
    title=f"{name} 연도별 기준값",
    xaxis_title="연도",
    yaxis_title="억원",
    height=420,
)
st.plotly_chart(fig, width="stretch")

st.markdown(f"### 목표 {metric} 계산기")

expected_year = st.number_input(
    "예상연도",
    min_value=2020,
    max_value=2035,
    value=datetime.today().year,
    step=1,
)

expected_base = st.number_input(
    {
        "POR": "예상 영업이익(억원)",
        "PER": "예상 당기순이익(억원)",
        "PBR": "예상 자본총계(억원)",
    }[metric],
    min_value=0.0,
    value=float(round(base_eok, 1)),
    step=10.0,
)

target_multiple = st.slider(
    f"목표 {metric}",
    min_value=1.0,
    max_value=30.0,
    value=8.0,
    step=0.5,
)

target_mcap = expected_base * target_multiple
target_price = (
    current_price * target_mcap / current_mcap_eok
    if current_price and current_mcap_eok > 0
    else None
)
upside = (
    (target_price / current_price - 1) * 100
    if target_price and current_price
    else None
)

t1, t2, t3, t4 = st.columns(4)
t1.metric("예상연도", f"{int(expected_year)}E")
t2.metric(f"목표 {metric}", f"{target_multiple:.1f}배")
t3.metric("목표 시가총액", f"{target_mcap:,.0f}억")
t4.metric(
    "목표 주가",
    f"{target_price:,.0f}원" if target_price else "-",
    f"{upside:.1f}%" if upside is not None else None,
)

st.caption(
    f"데이터 파일: {MARKET_PATH} / {FIN_PATH} · "
    "이 안정판은 앱 실행 중 외부 API를 호출하지 않습니다."
)
