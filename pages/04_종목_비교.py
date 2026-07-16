import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


st.set_page_config(page_title="종목 비교센터", page_icon="📊", layout="wide")

DATA_DIR = "data"
MARKET_DATA_CSV = os.path.join(DATA_DIR, "market_data.csv")
MARKET_HISTORY_CSV = os.path.join(DATA_DIR, "market_history.csv")
FINANCIAL_DATA_CSV = os.path.join(DATA_DIR, "financial_data.csv")
CONSENSUS_XLSX = os.path.join(DATA_DIR, "consensus.xlsx")


def clean_ticker(value):
    if value is None:
        return ""
    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


@st.cache_data(show_spinner=False, ttl=300)
def load_market():
    if not os.path.exists(MARKET_DATA_CSV):
        return pd.DataFrame()
    df = pd.read_csv(MARKET_DATA_CSV, dtype=str)
    rename_map = {}
    if "name" in df.columns and "종목명" not in df.columns:
        rename_map["name"] = "종목명"
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename_map["ticker"] = "종목코드"
    if "price" in df.columns and "현재가" not in df.columns:
        rename_map["price"] = "현재가"
    if "market_cap_eok" in df.columns and "현재시총_억원" not in df.columns:
        rename_map["market_cap_eok"] = "현재시총_억원"
    if rename_map:
        df = df.rename(columns=rename_map)
    if "종목명" not in df.columns or "종목코드" not in df.columns:
        return pd.DataFrame()
    df["종목코드"] = df["종목코드"].map(clean_ticker)
    for col in ["현재가", "현재시총_억원", "PER", "PBR"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.drop_duplicates("종목코드").reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=300)
def load_consensus():
    columns = ["종목명", "종목코드", "연도", "예상영업이익_억원", "목표POR", "출처", "업데이트일", "비고"]
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
    if "종목명" not in wide.columns or "종목코드" not in wide.columns:
        return pd.DataFrame(columns=columns)
    year_cols = [col for col in wide.columns if re.fullmatch(r"\d{4}E?", str(col).strip())]
    if not year_cols:
        return pd.DataFrame(columns=columns)
    id_cols = [col for col in ["종목명", "종목코드", "목표POR", "출처", "업데이트일", "비고"] if col in wide.columns]
    long_df = wide.melt(
        id_vars=id_cols,
        value_vars=year_cols,
        var_name="연도",
        value_name="예상영업이익_억원",
    )
    long_df["종목코드"] = long_df["종목코드"].map(clean_ticker)
    long_df["연도"] = pd.to_numeric(long_df["연도"].astype(str).str.extract(r"(\d{4})")[0], errors="coerce")
    long_df["예상영업이익_억원"] = pd.to_numeric(long_df["예상영업이익_억원"], errors="coerce")
    if "목표POR" not in long_df.columns:
        long_df["목표POR"] = None
    long_df["목표POR"] = pd.to_numeric(long_df["목표POR"], errors="coerce")
    for col in ["출처", "업데이트일", "비고"]:
        if col not in long_df.columns:
            long_df[col] = None
    return long_df.dropna(subset=["종목코드", "연도", "예상영업이익_억원"]).reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=300)
def load_actual_financials():
    if not os.path.exists(FINANCIAL_DATA_CSV):
        return pd.DataFrame()
    df = pd.read_csv(FINANCIAL_DATA_CSV, dtype={"ticker": str})
    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})
    if "ticker" not in df.columns or "year" not in df.columns:
        return pd.DataFrame()
    df["ticker"] = df["ticker"].map(clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["operating_income"] = pd.to_numeric(df.get("operating_income"), errors="coerce")
    return df


@st.cache_data(show_spinner=False, ttl=300)
def load_ytd_returns():
    columns = ["종목코드", "YTD주가상승률"]
    if not os.path.exists(MARKET_HISTORY_CSV):
        return pd.DataFrame(columns=columns)
    try:
        df = pd.read_csv(
            MARKET_HISTORY_CSV,
            usecols=["ticker", "date", "price"],
            dtype={"ticker": str},
        )
    except Exception:
        return pd.DataFrame(columns=columns)
    df["ticker"] = df["ticker"].map(clean_ticker)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df = df.dropna(subset=["ticker", "date", "price"])
    df = df[df["price"] > 0]
    df = df[df["date"].dt.year == datetime.today().year]
    if df.empty:
        return pd.DataFrame(columns=columns)
    rows = []
    for ticker, group in df.groupby("ticker"):
        group = group.sort_values("date")
        first_price = group.iloc[0]["price"]
        last_price = group.iloc[-1]["price"]
        if first_price and first_price > 0:
            rows.append({
                "종목코드": ticker,
                "YTD주가상승률": (last_price / first_price - 1) * 100,
            })
    return pd.DataFrame(rows)


def parse_stock_input(text, market):
    tokens = []
    for line in str(text).replace(",", "\n").splitlines():
        token = line.strip()
        if token:
            tokens.append(token)
    selected_rows = []
    seen = set()
    for token in tokens:
        code = clean_ticker(token)
        if code and len("".join(ch for ch in token if ch.isdigit())) >= 5:
            matched = market[market["종목코드"] == code]
        else:
            matched = market[market["종목명"].astype(str).str.lower().eq(token.lower())]
            if matched.empty:
                matched = market[
                    market["종목명"].astype(str).str.lower().str.contains(token.lower(), na=False)
                ]
        if matched.empty:
            continue
        row = matched.iloc[0]
        ticker = row["종목코드"]
        if ticker in seen:
            continue
        seen.add(ticker)
        selected_rows.append(row)
    if not selected_rows:
        return pd.DataFrame(columns=market.columns)
    return pd.DataFrame(selected_rows).reset_index(drop=True)


def build_comparison(selected_market, consensus, actuals, ytd_df, selected_year, default_target_por):
    rows = []
    previous_year = selected_year - 1
    for _, market_row in selected_market.iterrows():
        ticker = market_row["종목코드"]
        name = market_row["종목명"]
        cons_row = consensus[
            (consensus["종목코드"] == ticker)
            & (consensus["연도"] == selected_year)
        ]
        if cons_row.empty:
            continue
        cons_row = cons_row.iloc[0]
        expected_oi = float(cons_row["예상영업이익_억원"])
        current_mcap = pd.to_numeric(market_row.get("현재시총_억원"), errors="coerce")
        current_price = pd.to_numeric(market_row.get("현재가"), errors="coerce")
        previous_actual = actuals[
            (actuals["ticker"] == ticker)
            & (actuals["year"] == previous_year)
        ]
        previous_oi_eok = None
        if not previous_actual.empty:
            previous_value = previous_actual.iloc[0]["operating_income"]
            if pd.notna(previous_value):
                previous_oi_eok = previous_value / 100_000_000
        op_growth = None
        if previous_oi_eok and previous_oi_eok > 0:
            op_growth = (expected_oi / previous_oi_eok - 1) * 100
        current_por = None
        if pd.notna(current_mcap) and current_mcap > 0 and expected_oi > 0:
            current_por = current_mcap / expected_oi
        saved_target_por = pd.to_numeric(cons_row.get("목표POR"), errors="coerce")
        target_por = (
            float(saved_target_por)
            if pd.notna(saved_target_por) and saved_target_por > 0
            else float(default_target_por)
        )
        target_mcap = expected_oi * target_por
        target_price = None
        upside = None
        if pd.notna(current_price) and current_price > 0 and pd.notna(current_mcap) and current_mcap > 0:
            target_price = current_price * target_mcap / current_mcap
            upside = (target_price / current_price - 1) * 100
        ytd_row = ytd_df[ytd_df["종목코드"] == ticker]
        ytd_return = float(ytd_row.iloc[0]["YTD주가상승률"]) if not ytd_row.empty else None
        rows.append({
            "종목명": name,
            "종목코드": ticker,
            "현재가": current_price,
            "현재시총(억)": current_mcap,
            f"{selected_year}E 영업이익(억)": expected_oi,
            f"{selected_year}E 영업이익증가율(%)": op_growth,
            "YTD 주가상승률(%)": ytd_return,
            "현재 시총 기준 POR": current_por,
            "적용 목표 POR": target_por,
            "목표 시가총액(억)": target_mcap,
            "목표 주가(원)": target_price,
            "상승여력(%)": upside,
            "출처": cons_row.get("출처"),
            "업데이트일": cons_row.get("업데이트일"),
            "비고": cons_row.get("비고"),
        })
    return pd.DataFrame(rows)


st.title("📊 종목 비교센터")
st.caption(
    "컨센서스에 저장된 예상 영업이익과 YTD 주가 상승률, "
    "현재 POR 및 목표주가를 한 화면에서 비교합니다."
)

market = load_market()
consensus = load_consensus()
actuals = load_actual_financials()
ytd_df = load_ytd_returns()

if market.empty:
    st.error("data/market_data.csv를 읽지 못했습니다.")
    st.stop()

if consensus.empty:
    st.error(
        "data/consensus.xlsx에 저장된 컨센서스가 없습니다. "
        "컨센서스 관리 페이지에서 먼저 입력하세요."
    )
    st.stop()

left, right = st.columns([3, 1])

with left:
    input_text = st.text_area(
        "비교할 종목명 또는 종목코드",
        placeholder="심텍\n엘티씨\n코미코\n또는 심텍, 엘티씨, 코미코",
        height=140,
    )

with right:
    available_years = sorted(
        consensus["연도"].dropna().astype(int).unique().tolist()
    )
    current_year = datetime.today().year
    default_year_index = (
        available_years.index(current_year)
        if current_year in available_years
        else 0
    )
    selected_year = st.selectbox(
        "비교 컨센서스 연도",
        available_years,
        index=default_year_index,
    )
    default_target_por = st.number_input(
        "기본 목표 POR",
        min_value=1.0,
        max_value=100.0,
        value=8.0,
        step=0.5,
        help="엑셀에 목표 POR가 있으면 그 값을 사용하고, 비어 있으면 이 값을 사용합니다.",
    )

selected_market = parse_stock_input(input_text, market)

if input_text.strip() and selected_market.empty:
    st.warning("입력한 종목을 찾지 못했습니다.")

comparison = build_comparison(
    selected_market,
    consensus,
    actuals,
    ytd_df,
    int(selected_year),
    float(default_target_por),
)

if comparison.empty:
    st.info(
        "비교할 종목을 입력하세요. 선택 연도의 컨센서스가 저장된 종목만 결과에 표시됩니다."
    )
    st.stop()

growth_col = f"{selected_year}E 영업이익증가율(%)"
comparison = comparison.sort_values(
    growth_col,
    ascending=False,
    na_position="last",
).reset_index(drop=True)

m1, m2, m3, m4 = st.columns(4)
m1.metric("비교 종목", f"{len(comparison)}개")
m2.metric(
    "평균 영업이익 증가율",
    f"{comparison[growth_col].mean():.1f}%"
    if comparison[growth_col].notna().any()
    else "-",
)
m3.metric(
    "평균 YTD 수익률",
    f"{comparison['YTD 주가상승률(%)'].mean():.1f}%"
    if comparison["YTD 주가상승률(%)"].notna().any()
    else "-",
)
m4.metric(
    "평균 현재 POR",
    f"{comparison['현재 시총 기준 POR'].mean():.2f}배"
    if comparison["현재 시총 기준 POR"].notna().any()
    else "-",
)

st.markdown(f"### YTD 주가 상승률 vs {selected_year}E 영업이익 증가율")

fig = go.Figure()
fig.add_trace(
    go.Bar(
        name="YTD 주가 상승률",
        x=comparison["종목명"],
        y=comparison["YTD 주가상승률(%)"],
        text=comparison["YTD 주가상승률(%)"].map(
            lambda v: f"{v:.1f}%" if pd.notna(v) else ""
        ),
        textposition="auto",
    )
)
fig.add_trace(
    go.Bar(
        name=f"{selected_year}E 영업이익 증가율",
        x=comparison["종목명"],
        y=comparison[growth_col],
        text=comparison[growth_col].map(
            lambda v: f"{v:.1f}%" if pd.notna(v) else ""
        ),
        textposition="auto",
    )
)
fig.add_hline(y=0, line_width=1)
fig.update_layout(
    barmode="group",
    height=520,
    yaxis_title="증가율(%)",
    xaxis_title="종목",
    legend=dict(orientation="h", y=1.12, x=0.5, xanchor="center"),
    margin=dict(l=40, r=30, t=80, b=50),
)
st.plotly_chart(fig, use_container_width=True)

st.markdown("### 현재 POR 비교")
por_fig = go.Figure(
    go.Bar(
        x=comparison["종목명"],
        y=comparison["현재 시총 기준 POR"],
        text=comparison["현재 시총 기준 POR"].map(
            lambda v: f"{v:.2f}배" if pd.notna(v) else ""
        ),
        textposition="auto",
    )
)
por_fig.update_layout(
    height=430,
    yaxis_title="POR(배)",
    xaxis_title="종목",
    margin=dict(l=40, r=30, t=40, b=50),
)
st.plotly_chart(por_fig, use_container_width=True)

st.markdown("### 비교 상세")
display_df = comparison.copy()
st.dataframe(
    display_df,
    use_container_width=True,
    hide_index=True,
    column_config={
        "현재가": st.column_config.NumberColumn(format="%,.0f원"),
        "현재시총(억)": st.column_config.NumberColumn(format="%,.0f억"),
        f"{selected_year}E 영업이익(억)": st.column_config.NumberColumn(format="%,.0f억"),
        growth_col: st.column_config.NumberColumn(format="%.1f%%"),
        "YTD 주가상승률(%)": st.column_config.NumberColumn(format="%.1f%%"),
        "현재 시총 기준 POR": st.column_config.NumberColumn(format="%.2f배"),
        "적용 목표 POR": st.column_config.NumberColumn(format="%.1f배"),
        "목표 시가총액(억)": st.column_config.NumberColumn(format="%,.0f억"),
        "목표 주가(원)": st.column_config.NumberColumn(format="%,.0f원"),
        "상승여력(%)": st.column_config.NumberColumn(format="%.1f%%"),
    },
)

csv_data = display_df.to_csv(index=False, encoding="utf-8-sig")
st.download_button(
    "📥 비교 결과 CSV 다운로드",
    data=csv_data,
    file_name=f"종목비교_{selected_year}E.csv",
    mime="text/csv",
)
