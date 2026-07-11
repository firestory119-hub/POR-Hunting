import io
import os
import re
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import requests
import streamlit as st

try:
    from pykrx import stock
except Exception:
    stock = None

try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None


# =========================
# 기본 설정
# =========================
st.set_page_config(page_title="POR Hunting Pro v30 Preserve", layout="wide")

DATA_DIR = "data"
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")
API_KEY_FILE = os.path.join(DATA_DIR, "dart_api_key.txt")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
HISTORY_FILE = os.path.join(DATA_DIR, "search_history.csv")
FINANCIAL_CSV = os.path.join(DATA_DIR, "financial_data.csv")
MARKET_DATA_CSV = os.path.join(DATA_DIR, "market_data.csv")

os.makedirs(DATA_DIR, exist_ok=True)

st.title("POR Hunting Pro v30 Preserve")
st.caption("CSV 재무 + 주가/시총 + POR/PER/PBR 밴드 + 미래 POR 시뮬레이터")


# =========================
# 공통 함수
# =========================
def clean_num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace(" ", "")
    if s in ["", "-", "nan", "None"]:
        return None
    try:
        return float(s)
    except Exception:
        return None


def load_list_csv(path: str, columns: list[str]) -> pd.DataFrame:
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, dtype=str)
            for c in columns:
                if c not in df.columns:
                    df[c] = ""
            return df[columns].drop_duplicates().copy()
        except Exception:
            pass
    return pd.DataFrame(columns=columns)


def save_list_csv(df: pd.DataFrame, path: str):
    os.makedirs(DATA_DIR, exist_ok=True)
    df.drop_duplicates().to_csv(path, index=False, encoding="utf-8-sig")


def add_favorite(name: str, ticker: str):
    fav = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    new_row = pd.DataFrame([{
        "name": name,
        "ticker": ticker,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }])
    fav = pd.concat([fav, new_row], ignore_index=True)
    fav = fav.drop_duplicates(subset=["ticker"], keep="last")
    save_list_csv(fav, FAVORITES_FILE)


def remove_favorite(ticker: str):
    fav = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    fav = fav[fav["ticker"] != ticker].copy()
    save_list_csv(fav, FAVORITES_FILE)


def add_history(name: str, ticker: str):
    hist = load_list_csv(HISTORY_FILE, ["name", "ticker", "searched_at"])
    new_row = pd.DataFrame([{
        "name": name,
        "ticker": ticker,
        "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }])
    hist = pd.concat([hist, new_row], ignore_index=True)
    hist = hist.drop_duplicates(subset=["ticker"], keep="last")
    hist = hist.sort_values("searched_at", ascending=False).head(50)
    save_list_csv(hist, HISTORY_FILE)


# =========================
# 데이터 수집 함수
# =========================
@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_corp_codes(api_key: str = "") -> pd.DataFrame:
    """
    로컬 CSV에서 종목 목록을 구성합니다.
    앱 조회 중에는 DART 종목코드 API를 호출하지 않습니다.
    """
    frames = []

    if os.path.exists(FINANCIAL_CSV):
        try:
            fin = pd.read_csv(FINANCIAL_CSV, dtype={"ticker": str})
            if not fin.empty and {"name", "ticker"}.issubset(fin.columns):
                tmp = fin[["name", "ticker"]].dropna().drop_duplicates()
                frames.append(tmp)
        except Exception:
            pass

    if os.path.exists(MARKET_DATA_CSV):
        try:
            market = pd.read_csv(MARKET_DATA_CSV, dtype=str)
            name_col = "name" if "name" in market.columns else ("종목명" if "종목명" in market.columns else None)
            ticker_col = "ticker" if "ticker" in market.columns else ("종목코드" if "종목코드" in market.columns else None)
            if name_col and ticker_col:
                tmp = market[[name_col, ticker_col]].copy()
                tmp.columns = ["name", "ticker"]
                frames.append(tmp)
        except Exception:
            pass

    if not frames:
        raise RuntimeError(
            "종목 목록 CSV가 없습니다. data/financial_data.csv 또는 "
            "data/market_data.csv를 먼저 생성하세요."
        )

    df = pd.concat(frames, ignore_index=True).dropna().drop_duplicates("ticker")
    df["ticker"] = (
        df["ticker"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )
    df["name"] = df["name"].astype(str).str.strip()

    return df.rename(columns={
        "name": "corp_name",
        "ticker": "stock_code",
    }).assign(corp_code="")


@st.cache_data(show_spinner=False, ttl=60 * 60)
def fetch_financials(ticker: str, start_year: int, end_year: int) -> pd.DataFrame:
    """
    data/financial_data.csv에서 재무 데이터를 읽습니다.
    앱 조회 중에는 DART 재무 API를 호출하지 않습니다.
    """
    required = [
        "year", "revenue", "operating_income", "net_income", "equity",
        "operating_margin", "revenue_account_nm", "op_account_nm",
        "net_account_nm", "equity_account_nm", "fs_div",
    ]

    if not os.path.exists(FINANCIAL_CSV):
        raise RuntimeError(
            "data/financial_data.csv가 없습니다. "
            "GitHub Actions의 Update financial data를 먼저 실행하세요."
        )

    try:
        df = pd.read_csv(FINANCIAL_CSV, dtype={"ticker": str})
    except Exception as e:
        raise RuntimeError(f"financial_data.csv 읽기 실패: {e}") from e

    if df.empty:
        raise RuntimeError(
            "financial_data.csv가 비어 있습니다. "
            "GitHub Actions의 Update financial data를 먼저 실행하세요."
        )

    if "ticker" not in df.columns:
        raise RuntimeError("financial_data.csv에 ticker 열이 없습니다.")

    df["ticker"] = (
        df["ticker"].astype(str).str.replace(r"\.0$", "", regex=True).str.zfill(6)
    )
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for col in ["revenue", "operating_income", "net_income", "equity", "operating_margin"]:
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in required:
        if col not in df.columns:
            df[col] = None

    out = df[
        (df["ticker"] == str(ticker).zfill(6))
        & (df["year"] >= int(start_year))
        & (df["year"] <= int(end_year))
    ][required].copy()

    return out.sort_values("year").reset_index(drop=True)


@st.cache_data(show_spinner=False)
def get_current_shares_from_fdr(ticker: str) -> float | None:
    if fdr is None:
        return None

    try:
        listing = fdr.StockListing("KRX")
        row = listing[listing["Code"].astype(str).str.zfill(6) == ticker]
        if row.empty:
            return None

        for col in ["Stocks", "상장주식수"]:
            if col in row.columns:
                val = clean_num(row.iloc[0][col])
                if val and val > 0:
                    return val
    except Exception:
        return None

    return None


@st.cache_data(show_spinner=False)
def fetch_market_cap(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    """
    1순위: pykrx 시가총액 직접 조회
    2순위: FinanceDataReader 주가 × 현재 상장주식수
    """
    # 1순위 pykrx
    if stock is not None:
        try:
            df = stock.get_market_cap_by_date(start_date, end_date, ticker)
            if df is not None and not df.empty and "시가총액" in df.columns:
                df = df.reset_index()
                date_col = df.columns[0]
                df = df.rename(columns={date_col: "date", "시가총액": "market_cap"})
                df["date"] = pd.to_datetime(df["date"])

                if "종가" in df.columns:
                    df["price"] = df["종가"]
                else:
                    df["price"] = None

                df = df[["date", "market_cap", "price"]].dropna(subset=["date", "market_cap"])
                df = df.set_index("date").resample("W-FRI").last().dropna(subset=["market_cap"]).reset_index()
                if not df.empty:
                    return df
        except Exception:
            pass

    # 2순위 FinanceDataReader
    if fdr is None:
        raise RuntimeError("pykrx 시가총액 조회 실패. FinanceDataReader도 설치되어 있지 않습니다.")

    shares = get_current_shares_from_fdr(ticker)
    if shares is None or shares <= 0:
        raise RuntimeError("상장주식수를 가져오지 못했습니다. FinanceDataReader 설치/조회 상태를 확인하세요.")

    start = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    end = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    price = fdr.DataReader(ticker, start, end)
    if price is None or price.empty:
        return pd.DataFrame()

    price = price.reset_index()
    date_col = price.columns[0]
    price = price.rename(columns={date_col: "date"})

    close_col = "Close" if "Close" in price.columns else "종가"
    if close_col not in price.columns:
        return pd.DataFrame()

    price["date"] = pd.to_datetime(price["date"])
    price["price"] = price[close_col].astype(float)
    price["market_cap"] = price["price"] * float(shares)

    df = price[["date", "price", "market_cap"]].dropna()
    df = df.set_index("date").resample("W-FRI").last().dropna().reset_index()

    return df


@st.cache_data(show_spinner=False)
def get_current_price(ticker: str):
    if fdr is not None:
        try:
            df = fdr.DataReader(ticker)
            if df is not None and not df.empty:
                close_col = "Close" if "Close" in df.columns else "종가"
                if close_col in df.columns:
                    return float(df[close_col].dropna().iloc[-1])
        except Exception:
            pass
    return None


def make_valuation_df(
    mcap_df: pd.DataFrame,
    fin_df: pd.DataFrame,
    metric: str,
    forward_year: int | None,
    forward_oi_eok: float | None,
):
    out = mcap_df.copy()
    out["year"] = out["date"].dt.year

    fin_map = {}
    for _, r in fin_df.iterrows():
        y = int(r["year"])
        fin_map[y] = {
            "operating_income": r.get("operating_income"),
            "net_income": r.get("net_income"),
            "equity": r.get("equity"),
        }

    if metric == "POR":
        base_col = "operating_income"
    elif metric == "PER":
        base_col = "net_income"
    else:
        base_col = "equity"

    # v26: 선택한 지표 기준으로 미래 기준값 반영
    # POR=영업이익, PER=당기순이익, PBR=자본총계
    if forward_year and forward_oi_eok and forward_oi_eok > 0:
        fin_map.setdefault(int(forward_year), {})
        fin_map[int(forward_year)][base_col] = forward_oi_eok * 100_000_000

    latest_available = {}
    for y in sorted(out["year"].unique()):
        candidates = [
            yy
            for yy, vals in fin_map.items()
            if yy <= y
            and vals.get(base_col) is not None
            and pd.notna(vals.get(base_col))
            and vals.get(base_col) > 0
        ]
        latest_available[y] = fin_map[max(candidates)].get(base_col) if candidates else None

    out["base_value"] = out["year"].map(latest_available)

    if forward_year and forward_oi_eok and forward_oi_eok > 0:
        out.loc[out["year"] >= int(forward_year), "base_value"] = forward_oi_eok * 100_000_000

    out = out.dropna(subset=["base_value"])
    out = out[out["base_value"] > 0]
    out[metric] = out["market_cap"] / out["base_value"]
    out["ratio"] = out[metric]

    out = out[(out["ratio"] > 0) & (out["ratio"] < 300)]

    return out


def plot_valuation(val_df: pd.DataFrame, title: str, metric: str, chart_range: str, projected_info: dict | None = None):
    val_df = val_df.sort_values("date").copy()
    latest_date = val_df["date"].max()

    range_years = {
        "1년": 1,
        "3년": 3,
        "5년": 5,
        "10년": 10,
    }

    if chart_range in range_years:
        base_date = latest_date - pd.DateOffset(years=range_years[chart_range])
        plot_df = val_df[val_df["date"] >= base_date].copy()
    else:
        base_date = val_df["date"].min()
        plot_df = val_df.copy()

    if plot_df.empty:
        base_date = val_df["date"].min()
        plot_df = val_df.copy()

    mean = plot_df["ratio"].mean()
    std = plot_df["ratio"].std(ddof=0)

    if metric == "POR":
        base_label = "영업이익"
    elif metric == "PER":
        base_label = "당기순이익"
    else:
        base_label = "자본총계"

    if "price" not in plot_df.columns:
        plot_df["price"] = None

    custom_data = list(
        zip(
            plot_df["price"],
            plot_df["market_cap"] / 100_000_000,
            plot_df["base_value"] / 100_000_000,
        )
    )

    fig = go.Figure()

    fig.add_trace(
        go.Scatter(
            x=plot_df["date"],
            y=plot_df["ratio"],
            mode="lines",
            name=metric,
            line=dict(color="blue", width=1),
            customdata=custom_data,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "주가: %{customdata[0]:,.0f}원<br>"
                f"{metric}: " + "%{y:.2f}배<br>"
                "시가총액: %{customdata[1]:,.0f}억<br>"
                f"{base_label}: " + "%{customdata[2]:,.1f}억"
                "<extra></extra>"
            ),
        )
    )

    latest = plot_df.iloc[-1]
    latest_custom = [[
        latest["price"] if "price" in latest.index else None,
        latest["market_cap"] / 100_000_000,
        latest["base_value"] / 100_000_000,
    ]]

    fig.add_trace(
        go.Scatter(
            x=[latest["date"]],
            y=[latest["ratio"]],
            mode="markers",
            name=f"Latest {metric}",
            marker=dict(color="blue", size=8),
            customdata=latest_custom,
            hovertemplate=(
                "<b>%{x|%Y-%m-%d}</b><br>"
                "주가: %{customdata[0]:,.0f}원<br>"
                f"{metric}: " + "%{y:.2f}배<br>"
                "시가총액: %{customdata[1]:,.0f}억<br>"
                f"{base_label}: " + "%{customdata[2]:,.1f}억"
                "<extra></extra>"
            ),
        )
    )

    band_lines = [
        (f"Mean({chart_range})", mean, "deeppink"),
        ("+1σ", mean + std, "gray"),
        ("+2σ", mean + 2 * std, "gray"),
        ("+3σ", mean + 3 * std, "gray"),
        ("-1σ", mean - std, "gray"),
        ("-2σ", mean - 2 * std, "gray"),
    ]

    for label, y, color in band_lines:
        if pd.notna(y) and y > 0:
            fig.add_hline(
                y=y,
                line_dash="dash",
                line_color=color,
                annotation_text=f"{label}: {y:.2f}",
                annotation_position="right",
            )

    # v18: 미래 영역 음영 + 초록 예상점 + 예상선
    if projected_info is not None and projected_info.get("multiple") is not None:
        p_date = projected_info["date"]
        p_multiple = projected_info["multiple"]
        p_oi = projected_info["oi_eok"]
        p_mcap = projected_info["mcap_eok"]
        p_price = projected_info.get("price")
        p_year = projected_info["year"]

        # 미래 영역 음영
        fig.add_vrect(
            x0=latest["date"],
            x1=p_date + pd.DateOffset(months=3),
            fillcolor="LightGray",
            opacity=0.07,
            line_width=0,
            annotation_text="예상 구간",
            annotation_position="top left",
        )

        # 예상 POR 수평선
        fig.add_hline(
            y=p_multiple,
            line_dash="dot",
            line_color="green",
            line_width=2,
            annotation_text=f"{p_year}E 예상 {metric}: {p_multiple:.2f}",
            annotation_position="right",
        )

        # v20: 연결선은 제거하고 예상점과 수평 점선만 표시
        # 예상점 별도 표시
        fig.add_trace(
            go.Scatter(
                x=[p_date],
                y=[p_multiple],
                mode="markers",
                name=f"{p_year}E 예상 {metric}",
                marker=dict(color="green", size=14, symbol="circle"),
                customdata=[[p_price, p_mcap, p_oi]],
                hovertemplate=(
                    f"<b>{p_year}E 예상</b><br>"
                    f"{metric}: " + "%{y:.2f}배<br>"
                    "예상 주가: %{customdata[0]:,.0f}원<br>"
                    "예상 시가총액: %{customdata[1]:,.0f}억<br>"
                    f"예상 {base_label}: " + "%{customdata[2]:,.1f}억"
                    "<extra></extra>"
                ),
            )
        )

    fig.update_layout(
        title=f"{title} {metric} Band / 범위: {chart_range} / 초록점=미래 예상",
        height=650,
        xaxis_title="Date",
        yaxis_title=f"{metric}(배)",
        legend=dict(orientation="h", y=1.08, x=0.75),
        margin=dict(l=40, r=40, t=80, b=40),
        hovermode="x unified",
    )

    if projected_info is not None and projected_info.get("date") is not None:
        fig.update_xaxes(range=[plot_df["date"].min(), projected_info["date"] + pd.DateOffset(months=2)])

    fig.update_xaxes(
        hoverformat="%Y-%m-%d",
        tickformat="%Y-%m-%d",
    )

    return fig, mean, std, len(plot_df), base_date, plot_df


# =========================
# 사이드바
# =========================
with st.sidebar:
    st.header("설정")

    saved_key = ""
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                saved_key = f.read().strip()
        except Exception:
            saved_key = ""

    api_key = st.text_input(
        "OpenDART API Key (자동수집용, 앱 조회에는 불필요)",
        value=saved_key,
        type="password"
    )

    if api_key and api_key != saved_key:
        try:
            with open(API_KEY_FILE, "w", encoding="utf-8") as f:
                f.write(api_key.strip())
            st.success("API Key 저장됨")
        except Exception as e:
            st.warning(f"API Key 저장 실패: {e}")

    st.divider()
    st.subheader("즐겨찾기 / 최근검색")

    fav_df_side = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    hist_df_side = load_list_csv(HISTORY_FILE, ["name", "ticker", "searched_at"])

    quick_options = []
    quick_map = {}

    if not fav_df_side.empty:
        for _, r in fav_df_side.sort_values("saved_at", ascending=False).iterrows():
            label = f"★ {r['name']} ({r['ticker']})"
            quick_options.append(label)
            quick_map[label] = r["name"]

    if not hist_df_side.empty:
        for _, r in hist_df_side.sort_values("searched_at", ascending=False).head(10).iterrows():
            label = f"최근 {r['name']} ({r['ticker']})"
            quick_options.append(label)
            quick_map[label] = r["name"]

    selected_quick = st.selectbox(
        "빠른 선택",
        ["직접 입력"] + quick_options,
        index=0,
        key="quick_stock_select",
    )

    st.divider()

    valuation_metric = st.radio(
        "밴드 지표",
        ["POR", "PER", "PBR"],
        index=0,
        horizontal=True,
    )

    chart_range = st.radio(
        "차트 범위 / 평균 기준",
        ["1년", "3년", "5년", "10년", "전체"],
        index=4,
        horizontal=True,
        key="chart_range_selector",
    )

    years = st.slider("재무 조회 기간(년)", 5, 10, 10)

    forward_year = st.number_input(
        "예상연도(E)",
        value=datetime.today().year,
        min_value=2020,
        max_value=2035,
        step=1,
    )

    if valuation_metric == "POR":
        expected_base_label = "예상 영업이익"
    elif valuation_metric == "PER":
        expected_base_label = "예상 당기순이익"
    else:
        expected_base_label = "예상 자본총계"

    forward_oi_eok = st.number_input(f"{expected_base_label}(억원, 선택)", value=0.0, step=10.0)
    expected_mcap_eok = st.number_input("예상 시가총액(억원, 선택)", value=0.0, step=50.0)
    expected_price = st.number_input("예상 주가(원, 선택)", value=0.0, step=100.0)
    target_por_slider = st.slider(f"목표 {valuation_metric}", 1.0, 30.0, 8.0, 0.5)
    bear_por = st.number_input("보수 POR", value=5.0, step=0.5)
    base_por = st.number_input("적정 POR", value=8.0, step=0.5)
    bull_por = st.number_input("낙관 POR", value=12.0, step=0.5)
    target_multiple_manual = st.number_input("목표 배수 직접입력(선택)", value=0.0, step=0.5)

    st.caption("v30 Preserve: 기존 기능 유지 + 재무 CSV 초고속 모드")


# =========================
# 메인 화면
# =========================
default_query = "삼성전자"
try:
    if selected_quick != "직접 입력":
        default_query = quick_map.get(selected_quick, "삼성전자")
except Exception:
    pass

query = st.text_input(
    "Stock Name",
    value=default_query,
    help="종목명을 입력하고 엔터를 누르면 자동으로 조회됩니다."
)

run = bool(query.strip())

if run:
    with st.spinner("저장된 종목 목록을 불러오는 중..."):
        try:
            corp = get_corp_codes(api_key)
        except Exception as e:
            st.error(f"종목 목록 읽기 실패: {e}")
            st.stop()

    if corp.empty:
        st.error("종목 목록이 비어 있습니다. data/market_data.csv를 확인하세요.")
        st.stop()

    q = query.strip().lower()

    found = corp[
        corp["corp_name"].str.lower().str.contains(q, na=False)
        | corp["stock_code"].str.contains(q, na=False)
    ]

    if found.empty:
        st.error("검색 결과가 없습니다.")
        st.dataframe(corp.head(20))
        st.stop()

    found = found.drop_duplicates("stock_code").head(30)

    choice_label = st.selectbox(
        "검색 결과",
        [f"{r.corp_name} ({r.stock_code})" for _, r in found.iterrows()],
    )

    ticker = re.search(r"\((\d{6})\)", choice_label).group(1)
    row = found[found["stock_code"] == ticker].iloc[0]
    corp_code = row["corp_code"]
    name = row["corp_name"]

    add_history(name, ticker)

    fav_now = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    is_fav = (not fav_now.empty) and (ticker in fav_now["ticker"].tolist())

    fcol1, fcol2, fcol3 = st.columns([1, 1, 4])
    with fcol1:
        if not is_fav:
            if st.button("★ 즐겨찾기 추가", key=f"add_fav_{ticker}"):
                add_favorite(name, ticker)
                st.success(f"{name} 즐겨찾기 추가")
                st.rerun()
        else:
            st.success("★ 즐겨찾기")
    with fcol2:
        if is_fav:
            if st.button("☆ 즐겨찾기 해제", key=f"remove_fav_{ticker}"):
                remove_favorite(ticker)
                st.info(f"{name} 즐겨찾기 해제")
                st.rerun()

    end_year = datetime.today().year - 1
    start_year = end_year - years + 1
    start_date = f"{start_year}0101"
    end_date = datetime.today().strftime("%Y%m%d")

    with st.spinner("저장된 재무 데이터를 불러오는 중..."):
        try:
            fin_df = fetch_financials(ticker, start_year, end_year)
        except Exception as e:
            st.error(f"재무 데이터 읽기 실패: {e}")
            st.stop()

    with st.spinner("주가/시가총액 수집 중..."):
        try:
            mcap_df = fetch_market_cap(ticker, start_date, end_date)
        except Exception as e:
            st.error(f"시가총액 수집 실패: {e}")
            st.stop()

    if mcap_df.empty:
        st.error("시가총액 데이터를 가져오지 못했습니다.")
        st.stop()

    val_df = make_valuation_df(
        mcap_df,
        fin_df,
        valuation_metric,
        int(forward_year),
        forward_oi_eok if forward_oi_eok > 0 else None,
    )

    if val_df.empty:
        st.error("밴드 계산이 불가능합니다. 선택 지표의 기준값이 없거나 적자/마이너스일 수 있습니다.")
        st.dataframe(fin_df)
        st.stop()

    # v17.3: 미래 예상 POR 계산용 정보
    projected_info = None
    projected_multiple = None
    projected_mcap_eok = None

    if forward_oi_eok and forward_oi_eok > 0:
        latest_for_projection = val_df.iloc[-1]
        current_mcap_eok_for_projection = latest_for_projection["market_cap"] / 100_000_000

        current_price_for_projection = get_current_price(ticker)
        if not current_price_for_projection and "price" in latest_for_projection.index and pd.notna(latest_for_projection["price"]):
            current_price_for_projection = float(latest_for_projection["price"])

        # 예상 주가를 넣으면 현재 시총 대비 비율로 예상 시총을 역산
        if expected_price and expected_price > 0 and current_price_for_projection and current_price_for_projection > 0:
            projected_mcap_eok = current_mcap_eok_for_projection * (expected_price / current_price_for_projection)
            projected_price_for_display = expected_price
        else:
            projected_mcap_eok = expected_mcap_eok if expected_mcap_eok and expected_mcap_eok > 0 else current_mcap_eok_for_projection
            if current_price_for_projection and current_mcap_eok_for_projection > 0:
                projected_price_for_display = current_price_for_projection * (projected_mcap_eok / current_mcap_eok_for_projection)
            else:
                projected_price_for_display = None

        projected_multiple = projected_mcap_eok / forward_oi_eok

        projected_info = {
            "year": int(forward_year),
            "date": pd.Timestamp(year=int(forward_year), month=12, day=31),
            "oi_eok": float(forward_oi_eok),
            "mcap_eok": float(projected_mcap_eok),
            "price": float(projected_price_for_display) if projected_price_for_display else None,
            "multiple": float(projected_multiple),
        }

    st.subheader(f"{name} ({ticker})")

    c1, c2, c3, c4, c5, c6, c7, c8 = st.columns(8)
    latest = val_df.iloc[-1]

    latest_fin = fin_df.dropna(subset=["revenue"]).tail(1)
    latest_revenue = latest_fin.iloc[0]["revenue"] if not latest_fin.empty else None

    current_price = get_current_price(ticker)
    if not current_price and "price" in latest.index and pd.notna(latest["price"]):
        current_price = float(latest["price"])

    c1.metric("현재가", f"{current_price:,.0f}원" if current_price else "-")
    c2.metric(f"현재 {valuation_metric}", f"{latest['ratio']:.2f}")
    c3.metric("현재 시가총액", f"{latest['market_cap'] / 100_000_000:,.0f}억")
    c4.metric("적용 기준값", f"{latest['base_value'] / 100_000_000:,.1f}억")
    c5.metric("최근 매출액", f"{latest_revenue / 100_000_000:,.1f}억" if latest_revenue else "-")
    c6.metric("기준일", latest["date"].strftime("%Y-%m-%d"))
    c7.metric("차트 범위", chart_range)
    c8.metric(f"예상 {valuation_metric}", f"{projected_multiple:.2f}" if projected_multiple else "-")

    fig, mean, std, stat_count, stat_start_date, displayed_df = plot_valuation(
        val_df,
        f"{name} Multiple",
        valuation_metric,
        chart_range,
        projected_info,
    )

    st.plotly_chart(
        fig,
        use_container_width=True,
        key=f"{ticker}_{valuation_metric}_{chart_range}_{forward_year}_{forward_oi_eok}_{expected_mcap_eok}_{expected_price}_{projected_multiple}"
    )

    s1, s2, s3, s4 = st.columns(4)
    s1.metric(f"{chart_range} 평균 {valuation_metric}", f"{mean:.2f}")
    s2.metric("표준편차 σ", f"{std:.2f}")
    s3.metric("차트 시작일", stat_start_date.strftime("%Y-%m-%d"))
    s4.metric("표본 수", f"{stat_count}개")

    try:
        cur_ratio = displayed_df.iloc[-1]["ratio"]
        min_ratio = displayed_df["ratio"].min()
        max_ratio = displayed_df["ratio"].max()
        if max_ratio > min_ratio:
            location_pct = (cur_ratio - min_ratio) / (max_ratio - min_ratio) * 100
            st.progress(
                max(0, min(100, int(location_pct))),
                text=f"현재 {valuation_metric} 위치: 선택 범위 내 약 {location_pct:.1f}%"
            )
    except Exception:
        pass

    if projected_info is not None:
        st.markdown("### 미래 POR 시뮬레이션")
        p1, p2, p3 = st.columns(3)
        p1.metric(f"예상 {valuation_metric}", f"{projected_multiple:.2f}배")
        p2.metric("예상 주가", f"{projected_info.get('price'):,.0f}원" if projected_info.get("price") else "-")
        if current_price and projected_info.get("price"):
            p3.metric("상승여력", f"{(projected_info.get('price') / current_price - 1) * 100:.1f}%")
        else:
            p3.metric("상승여력", "-")

        with st.expander("예상 시나리오 상세", expanded=False):
            d1, d2, d3 = st.columns(3)
            d1.metric(f"{int(forward_year)}E {expected_base_label}", f"{forward_oi_eok:,.1f}억")
            d2.metric("예상 시가총액", f"{projected_mcap_eok:,.0f}억")
            d3.metric(f"현재 {valuation_metric} 대비", f"{(projected_multiple / latest['ratio'] - 1) * 100:.1f}%")

    # v20: 목표 POR 슬라이더 계산
    if forward_oi_eok and forward_oi_eok > 0:
        target_mcap_eok_by_slider = target_por_slider * forward_oi_eok
        target_price_by_slider = None
        target_upside_by_slider = None
        if current_price and latest["market_cap"] > 0:
            current_mcap_eok_for_slider = latest["market_cap"] / 100_000_000
            target_price_by_slider = current_price * (target_mcap_eok_by_slider / current_mcap_eok_for_slider)
            target_upside_by_slider = (target_price_by_slider / current_price - 1) * 100

        st.markdown("### 목표 POR 계산기")
        t1, t2, t3 = st.columns(3)
        t1.metric(f"목표 {valuation_metric}", f"{target_por_slider:.1f}배")
        t2.metric("목표 주가", f"{target_price_by_slider:,.0f}원" if target_price_by_slider else "-")
        t3.metric("상승여력", f"{target_upside_by_slider:.1f}%" if target_upside_by_slider is not None else "-")

        with st.expander("목표 POR 상세 계산", expanded=False):
            td1, td2 = st.columns(2)
            td1.metric("목표 시가총액", f"{target_mcap_eok_by_slider:,.0f}억")
            td2.metric("적용 영업이익", f"{forward_oi_eok:,.1f}억")


    # v22: POR Calculator Pro
    if valuation_metric in ["POR", "PER", "PBR"]:
        st.markdown(f"### {valuation_metric} Calculator Pro")

        calc_base_eok = None
        calc_base_label = "현재 적용 기준값"
        if forward_oi_eok and forward_oi_eok > 0:
            calc_base_eok = float(forward_oi_eok)
            calc_base_label = f"{int(forward_year)}E {expected_base_label}"
        elif latest["base_value"] and pd.notna(latest["base_value"]) and latest["base_value"] > 0:
            calc_base_eok = latest["base_value"] / 100_000_000

        if calc_base_eok and calc_base_eok > 0:
            current_mcap_eok_calc = latest["market_cap"] / 100_000_000
            current_por_calc = latest["ratio"]

            # 평균 대비 할인율
            avg_discount = None
            if mean and mean > 0:
                avg_discount = (current_por_calc / mean - 1) * 100

            cpa, cpb, cpc, cpd = st.columns(4)
            cpa.metric(calc_base_label, f"{calc_base_eok:,.1f}억")
            cpb.metric(f"현재 {valuation_metric}", f"{current_por_calc:.2f}배")
            cpc.metric(f"{chart_range} 평균 {valuation_metric}", f"{mean:.2f}배")
            cpd.metric("평균 대비", f"{avg_discount:.1f}%" if avg_discount is not None else "-")

            # 현재 POR 주변과 주요 POR 구간을 함께 표시
            por_values = sorted(set([
                3, 4, 5, 6, 7, 8, 9, 10, 12, 15,
                round(float(current_por_calc), 2),
                round(float(mean), 2) if pd.notna(mean) else None,
                round(float(target_por_slider), 2) if "target_por_slider" in globals() else None,
            ]))
            por_values = [v for v in por_values if v is not None and v > 0]

            rows = []
            for por_v in por_values:
                target_mcap_eok = por_v * calc_base_eok

                target_price = None
                upside_pct = None
                if current_price and current_mcap_eok_calc > 0:
                    target_price = current_price * (target_mcap_eok / current_mcap_eok_calc)
                    upside_pct = (target_price / current_price - 1) * 100

                if abs(por_v - current_por_calc) < 0.03:
                    tag = "현재"
                elif abs(por_v - mean) < 0.03:
                    tag = f"{chart_range} 평균"
                elif "target_por_slider" in globals() and abs(por_v - target_por_slider) < 0.03:
                    tag = "목표"
                else:
                    tag = ""

                rows.append(
                    {
                        "구분": tag,
                        "POR": por_v,
                        "목표 시가총액(억)": round(target_mcap_eok, 1),
                        "목표 주가(원)": round(target_price, 0) if target_price else None,
                        "상승여력(%)": round(upside_pct, 1) if upside_pct is not None else None,
                    }
                )

            calc_df = pd.DataFrame(rows).sort_values("POR").reset_index(drop=True)
            if valuation_metric != "POR":
                calc_df = calc_df.rename(columns={"POR": valuation_metric})

            def style_por_calculator(row):
                styles = [""] * len(row)
                if row["구분"] == "현재":
                    styles = ["background-color: #fff3cd; font-weight: 700"] * len(row)
                elif row["구분"] == f"{chart_range} 평균":
                    styles = ["background-color: #e7f1ff; font-weight: 700"] * len(row)
                elif row["구분"] == "목표":
                    styles = ["background-color: #d1e7dd; font-weight: 700"] * len(row)

                upside = row["상승여력(%)"]
                if pd.notna(upside):
                    idx = list(row.index).index("상승여력(%)")
                    if upside > 20:
                        styles[idx] = "color: #198754; font-weight: 700"
                    elif upside < 0:
                        styles[idx] = "color: #dc3545; font-weight: 700"
                    else:
                        styles[idx] = "color: #6c757d; font-weight: 700"
                return styles

            show_calc_df = calc_df.copy()
            styled = show_calc_df.style.apply(style_por_calculator, axis=1).format({
                valuation_metric: "{:.2f}",
                "목표 시가총액(억)": "{:,.1f}",
                "목표 주가(원)": "{:,.0f}",
                "상승여력(%)": "{:,.1f}%",
            }, na_rep="-")

            st.dataframe(styled, use_container_width=True, hide_index=True)

            st.caption("노란색=현재 POR, 파란색=선택 기간 평균 POR, 초록색=목표 POR입니다.")
        else:
            st.info(f"{valuation_metric} Calculator를 표시하려면 기준값 데이터가 필요합니다.")


    # v25: Fair Value / Tenbagger / Simple Report
    if valuation_metric == "POR":
        st.markdown("### 적정가 시나리오")

        scenario_base_eok = None
        if forward_oi_eok and forward_oi_eok > 0:
            scenario_base_eok = float(forward_oi_eok)
            scenario_label = f"{int(forward_year)}E {expected_base_label}"
        elif latest["base_value"] and pd.notna(latest["base_value"]) and latest["base_value"] > 0:
            scenario_base_eok = latest["base_value"] / 100_000_000
            scenario_label = "현재 적용 기준값"
        else:
            scenario_label = "영업이익"

        if scenario_base_eok and scenario_base_eok > 0 and current_price and latest["market_cap"] > 0:
            current_mcap_eok_s = latest["market_cap"] / 100_000_000

            scenario_rows = []
            for label, por_v in [("보수", bear_por), ("적정", base_por), ("낙관", bull_por)]:
                mcap_eok = por_v * scenario_base_eok
                price_v = current_price * (mcap_eok / current_mcap_eok_s)
                upside_v = (price_v / current_price - 1) * 100
                scenario_rows.append({
                    "시나리오": label,
                    "POR": por_v,
                    "시가총액(억)": mcap_eok,
                    "주가(원)": price_v,
                    "상승여력(%)": upside_v,
                })

            scen_df = pd.DataFrame(scenario_rows)

            fv1, fv2, fv3 = st.columns(3)
            for i, r in scen_df.iterrows():
                col = [fv1, fv2, fv3][i]
                col.metric(f"{r['시나리오']} 주가", f"{r['주가(원)']:,.0f}원", f"{r['상승여력(%)']:.1f}%")
                col.caption(f"POR {r['POR']:.1f}배 / 시총 {r['시가총액(억)']:,.0f}억")

            st.dataframe(
                scen_df.style.format({
                    "POR": "{:.1f}",
                    "시가총액(억)": "{:,.0f}",
                    "주가(원)": "{:,.0f}",
                    "상승여력(%)": "{:,.1f}%",
                }),
                use_container_width=True,
                hide_index=True,
            )

            st.markdown("### 텐베거 시뮬레이터")
            ten_mcap_eok = current_mcap_eok_s * 10
            ten_price = current_price * 10
            needed_oi_at_target_por = ten_mcap_eok / target_por_slider if target_por_slider else None

            ten1, ten2, ten3, ten4 = st.columns(4)
            ten1.metric("10배 시가총액", f"{ten_mcap_eok:,.0f}억")
            ten2.metric("10배 주가", f"{ten_price:,.0f}원")
            ten3.metric(f"목표 {valuation_metric}", f"{target_por_slider:.1f}배")
            ten4.metric("필요 영업이익", f"{needed_oi_at_target_por:,.1f}억" if needed_oi_at_target_por else "-")

            st.markdown("### AI 스타일 요약")
            discount_text = "-"
            if mean and mean > 0:
                discount_text = f"{(latest['ratio'] / mean - 1) * 100:.1f}%"

            summary_text = f"""
{name}의 현재 POR은 {latest['ratio']:.2f}배이고, 선택 기간 평균 POR은 {mean:.2f}배입니다.
현재 POR은 평균 대비 {discount_text} 수준입니다.

{scenario_label} {scenario_base_eok:,.1f}억을 기준으로 하면,
보수 POR {bear_por:.1f}배 기준 주가는 {scen_df.iloc[0]['주가(원)']:,.0f}원,
적정 POR {base_por:.1f}배 기준 주가는 {scen_df.iloc[1]['주가(원)']:,.0f}원,
낙관 POR {bull_por:.1f}배 기준 주가는 {scen_df.iloc[2]['주가(원)']:,.0f}원입니다.

목표 POR {target_por_slider:.1f}배에서 현재 시가총액의 10배가 되려면
영업이익은 약 {needed_oi_at_target_por:,.1f}억이 필요합니다.
"""
            st.text_area("자동 요약", summary_text.strip(), height=180)

    st.markdown("### 연도별 매출액 / 영업이익 / 당기순이익 / 자본총계")
    show_fin = fin_df.copy()
    show_fin["매출액(억)"] = (show_fin["revenue"] / 100_000_000).round(1)
    show_fin["영업이익(억)"] = (show_fin["operating_income"] / 100_000_000).round(1)
    show_fin["당기순이익(억)"] = (show_fin["net_income"] / 100_000_000).round(1)
    show_fin["자본총계(억)"] = (show_fin["equity"] / 100_000_000).round(1)
    show_fin["영업이익률(%)"] = show_fin["operating_margin"].round(1)

    st.dataframe(
        show_fin[
            [
                "year",
                "매출액(억)",
                "영업이익(억)",
                "당기순이익(억)",
                "자본총계(억)",
                "영업이익률(%)",
                "revenue_account_nm",
                "op_account_nm",
                "net_account_nm",
                "equity_account_nm",
                "fs_div",
            ]
        ],
        use_container_width=True,
    )

    st.markdown(f"### 목표 {valuation_metric}별 시가총액 / 목표가")
    target_base = latest["base_value"]

    base_multiples = [
        5,
        7,
        9,
        10,
        12,
        round(mean, 2),
        round(mean + std, 2),
        round(mean + 2 * std, 2),
    ]

    if projected_multiple is not None and projected_multiple > 0:
        base_multiples.append(round(float(projected_multiple), 2))

    if target_por_slider and target_por_slider > 0:
        base_multiples.append(round(float(target_por_slider), 2))

    if target_multiple_manual and target_multiple_manual > 0:
        base_multiples.append(round(float(target_multiple_manual), 2))

    targets = pd.DataFrame({valuation_metric: base_multiples})
    targets["목표 시가총액(억)"] = targets[valuation_metric] * target_base / 100_000_000

    current_mcap_eok = latest["market_cap"] / 100_000_000

    if current_price and current_mcap_eok > 0:
        targets["목표가(원)"] = (targets["목표 시가총액(억)"] / current_mcap_eok * current_price).round(0)
        targets["상승여력(%)"] = ((targets["목표가(원)"] / current_price - 1) * 100).round(1)
    else:
        targets["목표가(원)"] = None
        targets["상승여력(%)"] = None

    targets = targets.drop_duplicates(valuation_metric).sort_values(valuation_metric)

    show_targets = targets.copy()
    show_targets["목표 시가총액(억)"] = show_targets["목표 시가총액(억)"].round(1)
    show_targets["목표가(원)"] = show_targets["목표가(원)"].map(lambda x: f"{x:,.0f}" if pd.notna(x) else "-")

    st.dataframe(show_targets, use_container_width=True)

    with st.expander("즐겨찾기 / 최근 검색 관리", expanded=False):
        fav_manage = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
        hist_manage = load_list_csv(HISTORY_FILE, ["name", "ticker", "searched_at"])

        st.markdown("#### 즐겨찾기")
        st.dataframe(
            fav_manage.sort_values("saved_at", ascending=False) if not fav_manage.empty else fav_manage,
            use_container_width=True,
        )

        st.markdown("#### 최근 검색")
        st.dataframe(
            hist_manage.sort_values("searched_at", ascending=False) if not hist_manage.empty else hist_manage,
            use_container_width=True,
        )

else:
    st.info("종목명을 입력하면 저장된 CSV 데이터로 조회됩니다.")
