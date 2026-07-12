import os
from pathlib import Path

import numpy as np
import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="POR Scanner",
    page_icon="🔎",
    layout="wide",
)

DATA_DIR = Path("data")
HISTORY_FILE = DATA_DIR / "market_history.csv"
FINANCIAL_FILE = DATA_DIR / "financial_data.csv"
MARKET_FILE = DATA_DIR / "market_data.csv"


def clean_ticker(value):
    if value is None:
        return None

    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())

    return digits.zfill(6) if digits else None


def to_number(series):
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False),
        errors="coerce",
    )


@st.cache_data(show_spinner=False, ttl=600)
def load_history():
    if not HISTORY_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(
        HISTORY_FILE,
        dtype={"ticker": str},
    )

    rename = {}
    if "종목코드" in df.columns and "ticker" not in df.columns:
        rename["종목코드"] = "ticker"
    if "종목명" in df.columns and "name" not in df.columns:
        rename["종목명"] = "name"
    if "날짜" in df.columns and "date" not in df.columns:
        rename["날짜"] = "date"
    if "현재가" in df.columns and "price" not in df.columns:
        rename["현재가"] = "price"
    if "시가총액" in df.columns and "market_cap" not in df.columns:
        rename["시가총액"] = "market_cap"

    if rename:
        df = df.rename(columns=rename)

    required = {"ticker", "date", "market_cap"}
    if not required.issubset(df.columns):
        return pd.DataFrame()

    if "name" not in df.columns:
        df["name"] = df["ticker"]

    if "price" not in df.columns:
        df["price"] = np.nan

    df["ticker"] = df["ticker"].map(clean_ticker)
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["market_cap"] = to_number(df["market_cap"])
    df["price"] = to_number(df["price"])

    return (
        df.dropna(subset=["ticker", "date", "market_cap"])
        .drop_duplicates(["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False, ttl=600)
def load_financial():
    if not FINANCIAL_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(
        FINANCIAL_FILE,
        dtype={"ticker": str},
    )

    rename = {}
    if "종목코드" in df.columns and "ticker" not in df.columns:
        rename["종목코드"] = "ticker"
    if "종목명" in df.columns and "name" not in df.columns:
        rename["종목명"] = "name"
    if "연도" in df.columns and "year" not in df.columns:
        rename["연도"] = "year"
    if "매출액" in df.columns and "revenue" not in df.columns:
        rename["매출액"] = "revenue"
    if "영업이익" in df.columns and "operating_income" not in df.columns:
        rename["영업이익"] = "operating_income"
    if "당기순이익" in df.columns and "net_income" not in df.columns:
        rename["당기순이익"] = "net_income"
    if "자본총계" in df.columns and "equity" not in df.columns:
        rename["자본총계"] = "equity"

    if rename:
        df = df.rename(columns=rename)

    required = {
        "ticker",
        "year",
        "operating_income",
        "net_income",
        "equity",
    }

    if not required.issubset(df.columns):
        return pd.DataFrame()

    if "name" not in df.columns:
        df["name"] = df["ticker"]

    if "revenue" not in df.columns:
        df["revenue"] = np.nan

    df["ticker"] = df["ticker"].map(clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for column in [
        "revenue",
        "operating_income",
        "net_income",
        "equity",
    ]:
        df[column] = to_number(df[column])

    df["available_date"] = pd.to_datetime(
        (df["year"] + 1).astype("Int64").astype(str) + "-04-01",
        errors="coerce",
    )

    return (
        df.dropna(subset=["ticker", "year"])
        .drop_duplicates(["ticker", "year"], keep="last")
        .sort_values(["ticker", "year"])
        .reset_index(drop=True)
    )


@st.cache_data(show_spinner=False, ttl=600)
def load_market():
    if not MARKET_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(
        MARKET_FILE,
        dtype=str,
    )

    rename = {}
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename["ticker"] = "종목코드"
    if "name" in df.columns and "종목명" not in df.columns:
        rename["name"] = "종목명"
    if "market_cap_eok" in df.columns and "현재시총_억원" not in df.columns:
        rename["market_cap_eok"] = "현재시총_억원"

    if rename:
        df = df.rename(columns=rename)

    if "종목코드" not in df.columns:
        return pd.DataFrame()

    if "종목명" not in df.columns:
        df["종목명"] = df["종목코드"]

    df["종목코드"] = df["종목코드"].map(clean_ticker)

    if "현재시총_억원" in df.columns:
        df["현재시총_억원"] = to_number(df["현재시총_억원"])

    return df.drop_duplicates("종목코드")


def percentile_rank(values, current_value):
    values = pd.Series(values).dropna()
    values = values[values > 0]

    if values.empty or pd.isna(current_value):
        return np.nan

    return float((values <= current_value).mean() * 100)


def build_one_stock(ticker, history, financial):
    h = history[history["ticker"] == ticker].copy()
    f = financial[financial["ticker"] == ticker].copy()

    if h.empty or f.empty:
        return None

    h = h.sort_values("date")
    f = f.sort_values("year")

    latest_h = h.iloc[-1]
    latest_f = f.iloc[-1]

    market_cap = latest_h["market_cap"]
    operating_income = latest_f["operating_income"]
    net_income = latest_f["net_income"]
    equity = latest_f["equity"]

    current_por = (
        market_cap / operating_income
        if pd.notna(operating_income) and operating_income > 0
        else np.nan
    )
    current_per = (
        market_cap / net_income
        if pd.notna(net_income) and net_income > 0
        else np.nan
    )
    current_pbr = (
        market_cap / equity
        if pd.notna(equity) and equity > 0
        else np.nan
    )

    previous_f = f.iloc[-2] if len(f) >= 2 else None
    previous_op = (
        previous_f["operating_income"]
        if previous_f is not None
        else np.nan
    )

    op_growth = (
        (operating_income / previous_op - 1) * 100
        if (
            pd.notna(operating_income)
            and pd.notna(previous_op)
            and previous_op > 0
        )
        else np.nan
    )

    available_fin = f[
        [
            "available_date",
            "operating_income",
            "net_income",
            "equity",
        ]
    ].dropna(subset=["available_date"])

    daily = pd.merge_asof(
        h[["date", "market_cap"]].sort_values("date"),
        available_fin.sort_values("available_date"),
        left_on="date",
        right_on="available_date",
        direction="backward",
    )

    daily["POR"] = np.where(
        daily["operating_income"] > 0,
        daily["market_cap"] / daily["operating_income"],
        np.nan,
    )

    ten_year_start = h["date"].max() - pd.DateOffset(years=10)
    daily_10y = daily[daily["date"] >= ten_year_start]

    por_percentile = percentile_rank(
        daily_10y["POR"],
        current_por,
    )

    average_por = (
        daily_10y.loc[daily_10y["POR"] > 0, "POR"].mean()
    )

    latest_name = str(latest_h.get("name", ticker))

    return {
        "종목명": latest_name,
        "종목코드": ticker,
        "기준일": latest_h["date"],
        "현재가": latest_h.get("price", np.nan),
        "시가총액_억원": market_cap / 100_000_000,
        "현재POR": current_por,
        "현재PER": current_per,
        "현재PBR": current_pbr,
        "10년평균POR": average_por,
        "POR백분위": por_percentile,
        "최근영업이익_억원": operating_income / 100_000_000,
        "영업이익증가율": op_growth,
        "최근재무연도": int(latest_f["year"]),
        "일별표본수": int(len(daily_10y)),
    }


@st.cache_data(show_spinner=False, ttl=600)
def build_scanner():
    history = load_history()
    financial = load_financial()

    if history.empty or financial.empty:
        return pd.DataFrame()

    tickers = sorted(
        set(history["ticker"].dropna())
        & set(financial["ticker"].dropna())
    )

    rows = []

    for ticker in tickers:
        row = build_one_stock(
            ticker,
            history,
            financial,
        )
        if row:
            rows.append(row)

    result = pd.DataFrame(rows)

    if result.empty:
        return result

    market = load_market()
    if (
        not market.empty
        and "현재시총_억원" in market.columns
    ):
        market_small = market[
            ["종목코드", "종목명", "현재시총_억원"]
        ].copy()

        result = result.merge(
            market_small,
            on="종목코드",
            how="left",
            suffixes=("", "_시장"),
        )

        result["종목명"] = result["종목명_시장"].fillna(
            result["종목명"]
        )

        result["시가총액_억원"] = result[
            "현재시총_억원"
        ].fillna(result["시가총액_억원"])

        result = result.drop(
            columns=[
                "종목명_시장",
                "현재시총_억원",
            ],
            errors="ignore",
        )

    return result


st.title("🔎 POR Scanner")
st.caption(
    "현재 수집된 종목 가운데 역사적 저평가 구간과 "
    "기본 밸류 조건을 동시에 만족하는 후보를 찾습니다."
)

scanner = build_scanner()

if scanner.empty:
    st.error(
        "스캐너 데이터가 없습니다. "
        "market_history.csv와 financial_data.csv를 확인하세요."
    )
    st.stop()

with st.sidebar:
    st.header("스캔 조건")

    max_por_percentile = st.slider(
        "POR 백분위 상한",
        min_value=1,
        max_value=100,
        value=30,
        step=1,
        help="낮을수록 역사적으로 싼 구간입니다.",
    )

    max_por = st.number_input(
        "현재 POR 상한",
        min_value=0.0,
        value=10.0,
        step=0.5,
    )

    max_per = st.number_input(
        "현재 PER 상한",
        min_value=0.0,
        value=15.0,
        step=0.5,
    )

    max_pbr = st.number_input(
        "현재 PBR 상한",
        min_value=0.0,
        value=2.0,
        step=0.1,
    )

    max_market_cap = st.number_input(
        "시가총액 상한(억원)",
        min_value=0,
        value=20000,
        step=1000,
    )

    min_op_growth = st.number_input(
        "영업이익 증가율 하한(%)",
        value=0.0,
        step=5.0,
    )

    min_samples = st.number_input(
        "최소 일별 표본 수",
        min_value=1,
        value=100,
        step=50,
    )

    only_positive = st.checkbox(
        "POR·PER·PBR 양수만",
        value=True,
    )

    refresh = st.button(
        "데이터 다시 읽기",
        use_container_width=True,
    )

    if refresh:
        st.cache_data.clear()
        st.rerun()

filtered = scanner.copy()

filtered = filtered[
    filtered["POR백분위"].le(max_por_percentile)
    & filtered["현재POR"].le(max_por)
    & filtered["현재PER"].le(max_per)
    & filtered["현재PBR"].le(max_pbr)
    & filtered["시가총액_억원"].le(max_market_cap)
    & filtered["영업이익증가율"].ge(min_op_growth)
    & filtered["일별표본수"].ge(min_samples)
]

if only_positive:
    filtered = filtered[
        filtered["현재POR"].gt(0)
        & filtered["현재PER"].gt(0)
        & filtered["현재PBR"].gt(0)
    ]

filtered = filtered.sort_values(
    ["POR백분위", "현재POR", "영업이익증가율"],
    ascending=[True, True, False],
).reset_index(drop=True)

c1, c2, c3, c4 = st.columns(4)

c1.metric("수집 종목", f"{len(scanner):,}개")
c2.metric("조건 통과", f"{len(filtered):,}개")
c3.metric(
    "중앙 POR 백분위",
    (
        f"{filtered['POR백분위'].median():.1f}%"
        if not filtered.empty
        else "-"
    ),
)
c4.metric(
    "중앙 영업이익 증가율",
    (
        f"{filtered['영업이익증가율'].median():.1f}%"
        if not filtered.empty
        else "-"
    ),
)

st.subheader("조건 통과 종목")

if filtered.empty:
    st.info(
        "현재 조건을 만족하는 종목이 없습니다. "
        "사이드바 조건을 조금 완화해 보세요."
    )
else:
    display = filtered.copy()

    display["기준일"] = pd.to_datetime(
        display["기준일"]
    ).dt.strftime("%Y-%m-%d")

    for column in [
        "현재가",
        "시가총액_억원",
        "현재POR",
        "현재PER",
        "현재PBR",
        "10년평균POR",
        "POR백분위",
        "최근영업이익_억원",
        "영업이익증가율",
    ]:
        display[column] = pd.to_numeric(
            display[column],
            errors="coerce",
        ).round(2)

    columns = [
        "종목명",
        "종목코드",
        "기준일",
        "현재가",
        "시가총액_억원",
        "현재POR",
        "10년평균POR",
        "POR백분위",
        "현재PER",
        "현재PBR",
        "최근영업이익_억원",
        "영업이익증가율",
        "최근재무연도",
        "일별표본수",
    ]

    st.dataframe(
        display[columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "POR백분위": st.column_config.ProgressColumn(
                "POR 백분위",
                min_value=0,
                max_value=100,
                format="%.1f%%",
            ),
            "영업이익증가율": st.column_config.NumberColumn(
                "영업이익 증가율",
                format="%.1f%%",
            ),
            "현재POR": st.column_config.NumberColumn(
                "현재 POR",
                format="%.2f",
            ),
            "현재PER": st.column_config.NumberColumn(
                "현재 PER",
                format="%.2f",
            ),
            "현재PBR": st.column_config.NumberColumn(
                "현재 PBR",
                format="%.2f",
            ),
        },
    )

    csv_data = display[columns].to_csv(
        index=False,
        encoding="utf-8-sig",
    )

    st.download_button(
        "스캔 결과 CSV 다운로드",
        data=csv_data,
        file_name="por_scanner_result.csv",
        mime="text/csv",
    )

with st.expander("계산 기준"):
    st.markdown(
        """
- **현재 POR** = 최신 시가총액 ÷ 최신 연간 영업이익
- **현재 PER** = 최신 시가총액 ÷ 최신 연간 순이익
- **현재 PBR** = 최신 시가총액 ÷ 최신 자본총계
- **POR 백분위**는 최근 10년 일별 POR 중 현재 값 이하의 비율입니다.
- 연간 재무정보는 다음 해 4월 1일부터 시장에 반영된 것으로 간주해 과거 데이터에 연결합니다.
- 스캐너는 현재 `market_history.csv`와 `financial_data.csv`에 모두 존재하는 종목만 대상으로 합니다.
        """
    )
