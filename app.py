# -*- coding: utf-8 -*-
"""
POR Hunting Pro v28 Stable
- 자동 조회 제거: 버튼을 눌러야 DART/주가 수집 시작
- DART 요청 timeout 적용
- API 지연/오류 시 앱이 멈추지 않도록 예외 처리
- POR/PER/PBR 밴드 + 미래 예상값 계산
"""

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

st.set_page_config(page_title="POR Hunting Pro v28", layout="wide")

DATA_DIR = "data"
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")
API_KEY_FILE = os.path.join(DATA_DIR, "dart_api_key.txt")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
HISTORY_FILE = os.path.join(DATA_DIR, "search_history.csv")
os.makedirs(DATA_DIR, exist_ok=True)

DART_TIMEOUT = 10
MARKET_TIMEOUT = 15

st.title("POR Hunting Pro v28")
st.caption("안정판: 버튼 실행 + DART timeout + 오류 방어 / POR·PER·PBR 밴드")


def clean_num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace(" ", "").replace("\n", "")
    if s in ["", "-", "nan", "None"]:
        return None
    try:
        return float(s)
    except Exception:
        return None


def load_list_csv(path, columns):
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


def save_list_csv(df, path):
    os.makedirs(DATA_DIR, exist_ok=True)
    df.drop_duplicates().to_csv(path, index=False, encoding="utf-8-sig")


def add_history(name, ticker):
    hist = load_list_csv(HISTORY_FILE, ["name", "ticker", "searched_at"])
    new_row = pd.DataFrame([{
        "name": name,
        "ticker": ticker,
        "searched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }])
    hist = pd.concat([hist, new_row], ignore_index=True)
    hist = hist.drop_duplicates(subset=["ticker"], keep="last")
    hist = hist.sort_values("searched_at", ascending=False).head(50)
    save_list_csv(hist, HISTORY_FILE)


def add_favorite(name, ticker):
    fav = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    new_row = pd.DataFrame([{
        "name": name,
        "ticker": ticker,
        "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }])
    fav = pd.concat([fav, new_row], ignore_index=True)
    fav = fav.drop_duplicates(subset=["ticker"], keep="last")
    save_list_csv(fav, FAVORITES_FILE)


def remove_favorite(ticker):
    fav = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
    fav = fav[fav["ticker"] != ticker].copy()
    save_list_csv(fav, FAVORITES_FILE)


@st.cache_data(show_spinner=False, ttl=60 * 60 * 24)
def get_corp_codes(api_key: str) -> pd.DataFrame:
    if os.path.exists(CORP_CACHE):
        try:
            df = pd.read_csv(CORP_CACHE, dtype={"corp_code": str, "stock_code": str})
            if not df.empty:
                df["stock_code"] = df["stock_code"].astype(str).str.zfill(6)
                return df
        except Exception:
            pass

    url = "https://opendart.fss.or.kr/api/corpCode.xml"
    try:
        r = requests.get(url, params={"crtfc_key": api_key}, timeout=DART_TIMEOUT)
        r.raise_for_status()
    except requests.exceptions.Timeout:
        raise RuntimeError("DART 종목코드 요청이 10초를 초과했습니다. 잠시 후 다시 시도하세요.")
    except Exception as e:
        raise RuntimeError(f"DART 종목코드 요청 실패: {e}")

    try:
        z = zipfile.ZipFile(io.BytesIO(r.content))
    except Exception:
        txt = r.text[:300]
        raise RuntimeError(f"DART 응답 오류. API Key를 확인하세요. 응답: {txt}")

    xml_data = z.read(z.namelist()[0])
    root = ET.fromstring(xml_data)

    rows = []
    for item in root.findall("list"):
        corp_code = item.findtext("corp_code", "")
        corp_name = item.findtext("corp_name", "")
        stock_code = item.findtext("stock_code", "").strip()
        modify_date = item.findtext("modify_date", "")
        if len(stock_code) == 6:
            rows.append([corp_code, corp_name, stock_code, modify_date])

    df = pd.DataFrame(rows, columns=["corp_code", "corp_name", "stock_code", "modify_date"])
    df.to_csv(CORP_CACHE, index=False, encoding="utf-8-sig")
    return df


def pick_accounts(items, fs_div_label):
    revenue_candidates = []
    op_candidates = []
    net_candidates = []
    equity_candidates = []

    for it in items:
        acc = str(it.get("account_nm", "")).strip()
        acc_id = str(it.get("account_id", "")).strip().lower()
        sj = str(it.get("sj_div", "")).strip()
        acc_norm = acc.replace(" ", "").replace("\n", "")

        val = clean_num(it.get("thstrm_amount"))
        if val is None:
            val = clean_num(it.get("thstrm_add_amount"))
        if val is None:
            continue

        is_income_stmt = (not sj) or sj in ["IS", "CIS"]
        is_balance_sheet = (not sj) or sj in ["BS"]

        if is_income_stmt:
            if (
                acc_norm in ["매출액", "수익(매출액)", "영업수익"]
                or "매출액" in acc_norm
                or "revenue" in acc_id
                or "sales" in acc_id
            ):
                revenue_candidates.append((acc, acc_id, val, fs_div_label))

            if (
                "영업이익" in acc_norm
                or "operatingincome" in acc_id
                or "operatingprofit" in acc_id
                or "profitlossfromoperatingactivities" in acc_id
            ):
                op_candidates.append((acc, acc_id, val, fs_div_label))

            if (
                "당기순이익" in acc_norm
                or acc_id in ["ifrs-full_profitloss", "profitloss"]
                or "profitlossattributabletoownersofparent" in acc_id
            ):
                net_candidates.append((acc, acc_id, val, fs_div_label))

        if is_balance_sheet:
            if (
                acc_norm in ["자본총계", "자본"]
                or "자본총계" in acc_norm
                or acc_id in ["ifrs-full_equity", "ifrs-full_equityattributabletoownersofparent"]
                or acc_id.endswith("equity")
            ):
                equity_candidates.append((acc, acc_id, val, fs_div_label))

    def pick_short(candidates):
        return sorted(candidates, key=lambda x: (len(x[0]), x[0]))[0] if candidates else None

    revenue = pick_short(revenue_candidates)
    operating = pick_short(op_candidates)
    net_income = pick_short(net_candidates)

    equity = None
    if equity_candidates:
        exact = [x for x in equity_candidates if x[0].replace(" ", "") == "자본총계"]
        equity = exact[0] if exact else pick_short(equity_candidates)

    return revenue, operating, net_income, equity


@st.cache_data(show_spinner=False, ttl=60 * 60)
def fetch_financials(api_key: str, corp_code: str, start_year: int, end_year: int) -> pd.DataFrame:
    rows = []

    for year in range(start_year, end_year + 1):
        revenue_found = None
        op_found = None
        net_found = None
        equity_found = None
        status_note = ""

        for fs_div in ["CFS", "OFS"]:
            url = "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json"
            params = {
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": fs_div,
            }
            try:
                r = requests.get(url, params=params, timeout=DART_TIMEOUT)
                data = r.json()
            except requests.exceptions.Timeout:
                status_note = f"{year}년 {fs_div} DART timeout"
                continue
            except Exception as e:
                status_note = f"{year}년 {fs_div} 오류: {e}"
                continue

            if data.get("status") != "000":
                status_note = data.get("message", "DART no data")
                continue

            rev, op, net, equity = pick_accounts(data.get("list", []), fs_div)
            if revenue_found is None and rev:
                revenue_found = rev
            if op_found is None and op:
                op_found = op
            if net_found is None and net:
                net_found = net
            if equity_found is None and equity:
                equity_found = equity
            if revenue_found and op_found and net_found and equity_found:
                break

        if not (revenue_found and op_found and net_found and equity_found):
            url = "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json"
            params = {
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
            }
            try:
                r = requests.get(url, params=params, timeout=DART_TIMEOUT)
                data = r.json()
                if data.get("status") == "000":
                    rev, op, net, equity = pick_accounts(data.get("list", []), "FALLBACK")
                    if revenue_found is None and rev:
                        revenue_found = rev
                    if op_found is None and op:
                        op_found = op
                    if net_found is None and net:
                        net_found = net
                    if equity_found is None and equity:
                        equity_found = equity
            except requests.exceptions.Timeout:
                status_note = f"{year}년 fallback DART timeout"
            except Exception as e:
                status_note = f"{year}년 fallback 오류: {e}"

        revenue = revenue_found[2] if revenue_found else None
        operating_income = op_found[2] if op_found else None
        net_income = net_found[2] if net_found else None
        equity = equity_found[2] if equity_found else None
        margin = operating_income / revenue * 100 if revenue and operating_income else None

        rows.append({
            "year": year,
            "revenue": revenue,
            "operating_income": operating_income,
            "net_income": net_income,
            "equity": equity,
            "operating_margin": margin,
            "revenue_account_nm": revenue_found[0] if revenue_found else None,
            "op_account_nm": op_found[0] if op_found else None,
            "net_account_nm": net_found[0] if net_found else None,
            "equity_account_nm": equity_found[0] if equity_found else None,
            "fs_div": op_found[3] if op_found else (revenue_found[3] if revenue_found else None),
            "note": status_note,
        })

    return pd.DataFrame(rows)


@st.cache_data(show_spinner=False, ttl=60 * 60)
def get_current_shares_from_fdr(ticker: str):
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


@st.cache_data(show_spinner=False, ttl=60 * 30)
def fetch_market_cap(ticker: str, start_date: str, end_date: str) -> pd.DataFrame:
    if stock is not None:
        try:
            df = stock.get_market_cap_by_date(start_date, end_date, ticker)
            if df is not None and not df.empty and "시가총액" in df.columns:
                df = df.reset_index()
                date_col = df.columns[0]
                df = df.rename(columns={date_col: "date", "시가총액": "market_cap"})
                df["date"] = pd.to_datetime(df["date"])
                df["price"] = df["종가"] if "종가" in df.columns else None
                df = df[["date", "market_cap", "price"]].dropna(subset=["date", "market_cap"])
                df = df.set_index("date").resample("W-FRI").last().dropna(subset=["market_cap"]).reset_index()
                if not df.empty:
                    return df
        except Exception:
            pass

    if fdr is None:
        raise RuntimeError("pykrx 조회 실패, FinanceDataReader도 설치되어 있지 않습니다.")

    shares = get_current_shares_from_fdr(ticker)
    if shares is None or shares <= 0:
        raise RuntimeError("상장주식수를 가져오지 못했습니다.")

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


@st.cache_data(show_spinner=False, ttl=60 * 10)
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


def make_valuation_df(mcap_df, fin_df, metric, forward_year=None, forward_base_eok=None):
    out = mcap_df.copy()
    out["year"] = out["date"].dt.year

    base_col = {"POR": "operating_income", "PER": "net_income", "PBR": "equity"}[metric]
    fin_map = {}
    for _, r in fin_df.iterrows():
        y = int(r["year"])
        fin_map[y] = {
            "operating_income": r.get("operating_income"),
            "net_income": r.get("net_income"),
            "equity": r.get("equity"),
        }

    if forward_year and forward_base_eok and forward_base_eok > 0:
        fin_map.setdefault(int(forward_year), {})
        fin_map[int(forward_year)][base_col] = forward_base_eok * 100_000_000

    latest_available = {}
    for y in sorted(out["year"].unique()):
        candidates = [
            yy for yy, vals in fin_map.items()
            if yy <= y and vals.get(base_col) is not None and pd.notna(vals.get(base_col)) and vals.get(base_col) > 0
        ]
        latest_available[y] = fin_map[max(candidates)].get(base_col) if candidates else None

    out["base_value"] = out["year"].map(latest_available)
    if forward_year and forward_base_eok and forward_base_eok > 0:
        out.loc[out["year"] >= int(forward_year), "base_value"] = forward_base_eok * 100_000_000

    out = out.dropna(subset=["base_value"])
    out = out[out["base_value"] > 0]
    out["ratio"] = out["market_cap"] / out["base_value"]
    out = out[(out["ratio"] > 0) & (out["ratio"] < 300)]
    return out


def plot_valuation(val_df, title, metric, chart_range, projected_info=None):
    val_df = val_df.sort_values("date").copy()
    latest_date = val_df["date"].max()
    range_years = {"1년": 1, "3년": 3, "5년": 5, "10년": 10}

    if chart_range in range_years:
        base_date = latest_date - pd.DateOffset(years=range_years[chart_range])
        plot_df = val_df[val_df["date"] >= base_date].copy()
    else:
        plot_df = val_df.copy()

    if plot_df.empty:
        plot_df = val_df.copy()

    mean = plot_df["ratio"].mean()
    std = plot_df["ratio"].std(ddof=0)
    base_label = {"POR": "영업이익", "PER": "당기순이익", "PBR": "자본총계"}[metric]

    custom_data = list(zip(plot_df["price"], plot_df["market_cap"] / 100_000_000, plot_df["base_value"] / 100_000_000))
    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=plot_df["date"], y=plot_df["ratio"], mode="lines", name=metric,
        customdata=custom_data,
        hovertemplate=(
            "<b>%{x|%Y-%m-%d}</b><br>"
            "주가: %{customdata[0]:,.0f}원<br>"
            f"{metric}: " + "%{y:.2f}배<br>"
            "시가총액: %{customdata[1]:,.0f}억<br>"
            f"{base_label}: " + "%{customdata[2]:,.1f}억<extra></extra>"
        ),
    ))

    latest = plot_df.iloc[-1]
    fig.add_trace(go.Scatter(
        x=[latest["date"]], y=[latest["ratio"]], mode="markers", name=f"Latest {metric}",
        marker=dict(size=9),
    ))

    for label, y in [
        (f"Mean({chart_range})", mean), ("+1σ", mean + std), ("+2σ", mean + 2 * std),
        ("+3σ", mean + 3 * std), ("-1σ", mean - std), ("-2σ", mean - 2 * std),
    ]:
        if pd.notna(y) and y > 0:
            fig.add_hline(y=y, line_dash="dash", annotation_text=f"{label}: {y:.2f}", annotation_position="right")

    if projected_info and projected_info.get("multiple") is not None:
        p_date = projected_info["date"]
        p_multiple = projected_info["multiple"]
        fig.add_vrect(x0=latest["date"], x1=p_date + pd.DateOffset(months=3), opacity=0.08, line_width=0)
        fig.add_hline(y=p_multiple, line_dash="dot", line_width=2, annotation_text=f"{projected_info['year']}E 예상 {metric}: {p_multiple:.2f}", annotation_position="right")
        fig.add_trace(go.Scatter(
            x=[p_date], y=[p_multiple], mode="markers", name=f"{projected_info['year']}E 예상",
            marker=dict(size=14, symbol="circle"),
            customdata=[[projected_info.get("price"), projected_info.get("mcap_eok"), projected_info.get("base_eok")]],
            hovertemplate=(
                f"<b>{projected_info['year']}E 예상</b><br>"
                f"{metric}: " + "%{y:.2f}배<br>"
                "예상 주가: %{customdata[0]:,.0f}원<br>"
                "예상 시가총액: %{customdata[1]:,.0f}억<br>"
                f"예상 {base_label}: " + "%{customdata[2]:,.1f}억<extra></extra>"
            ),
        ))

    fig.update_layout(
        title=f"{title} {metric} Band / 범위: {chart_range}",
        height=650,
        xaxis_title="Date",
        yaxis_title=f"{metric}(배)",
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08, x=0.75),
        margin=dict(l=40, r=40, t=80, b=40),
    )
    if projected_info and projected_info.get("date") is not None:
        fig.update_xaxes(range=[plot_df["date"].min(), projected_info["date"] + pd.DateOffset(months=2)])
    return fig, mean, std, len(plot_df)


with st.sidebar:
    st.header("설정")
    saved_key = ""
    if os.path.exists(API_KEY_FILE):
        try:
            with open(API_KEY_FILE, "r", encoding="utf-8") as f:
                saved_key = f.read().strip()
        except Exception:
            saved_key = ""

    api_key = st.text_input("OpenDART API Key", value=saved_key, type="password")
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
    quick_options, quick_map = [], {}
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
    selected_quick = st.selectbox("빠른 선택", ["직접 입력"] + quick_options, index=0)

    st.divider()
    valuation_metric = st.radio("밴드 지표", ["POR", "PER", "PBR"], index=0, horizontal=True)
    chart_range = st.radio("차트 범위 / 평균 기준", ["1년", "3년", "5년", "10년", "전체"], index=4, horizontal=True)
    years = st.slider("재무 조회 기간(년)", 5, 10, 10)
    forward_year = st.number_input("예상연도(E)", value=datetime.today().year, min_value=2020, max_value=2035, step=1)
    expected_base_label = {"POR": "예상 영업이익", "PER": "예상 당기순이익", "PBR": "예상 자본총계"}[valuation_metric]
    forward_base_eok = st.number_input(f"{expected_base_label}(억원, 선택)", value=0.0, step=10.0)
    expected_mcap_eok = st.number_input("예상 시가총액(억원, 선택)", value=0.0, step=50.0)
    expected_price = st.number_input("예상 주가(원, 선택)", value=0.0, step=100.0)
    target_multiple = st.slider(f"목표 {valuation_metric}", 1.0, 30.0, 8.0, 0.5)
    st.caption("v28: 자동 조회 없음. 버튼을 눌러야 수집합니다.")


default_query = "삼성전자"
if selected_quick != "직접 입력":
    default_query = quick_map.get(selected_quick, "삼성전자")

query = st.text_input("Stock Name", value=default_query)
run = st.button("데이터 수집 / 차트 생성", type="primary")

if not run:
    st.info("종목명을 입력하고 버튼을 누르면 조회를 시작합니다. 이제 앱을 열자마자 DART가 자동 실행되지 않습니다.")
    st.stop()

if not api_key:
    st.warning("왼쪽에 OpenDART API Key를 입력하세요.")
    st.stop()

if not query.strip():
    st.warning("종목명을 입력하세요.")
    st.stop()

with st.spinner("DART 종목 목록을 불러오는 중..."):
    try:
        corp = get_corp_codes(api_key.strip())
    except Exception as e:
        st.error(f"DART 종목 목록 수집 실패: {e}")
        st.stop()

q = query.strip().lower()
found = corp[
    corp["corp_name"].str.lower().str.contains(q, na=False)
    | corp["stock_code"].astype(str).str.zfill(6).str.contains(q, na=False)
]

if found.empty:
    st.error("검색 결과가 없습니다.")
    st.dataframe(corp.head(20), use_container_width=True)
    st.stop()

found = found.drop_duplicates("stock_code").head(30)
choice_label = st.selectbox("검색 결과", [f"{r.corp_name} ({r.stock_code})" for _, r in found.iterrows()])
ticker = re.search(r"\((\d{6})\)", choice_label).group(1)
row = found[found["stock_code"].astype(str).str.zfill(6) == ticker].iloc[0]
corp_code = row["corp_code"]
name = row["corp_name"]
add_history(name, ticker)

fav_now = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
is_fav = (not fav_now.empty) and (ticker in fav_now["ticker"].tolist())
fcol1, fcol2, _ = st.columns([1, 1, 4])
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

end_year = datetime.today().year
start_year = end_year - int(years) + 1
start_date = f"{start_year}0101"
end_date = datetime.today().strftime("%Y%m%d")

with st.spinner("DART에서 매출액/영업이익/순이익/자본 수집 중..."):
    fin_df = fetch_financials(api_key.strip(), corp_code, start_year, end_year)

if fin_df.empty:
    st.error("DART 재무 데이터를 가져오지 못했습니다.")
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

val_df = make_valuation_df(mcap_df, fin_df, valuation_metric, int(forward_year), forward_base_eok if forward_base_eok > 0 else None)
if val_df.empty:
    st.error("밴드 계산이 불가능합니다. 선택 지표의 기준값이 없거나 적자/마이너스일 수 있습니다.")
    st.dataframe(fin_df, use_container_width=True)
    st.stop()

latest = val_df.iloc[-1]
current_price = get_current_price(ticker)
if not current_price and "price" in latest.index and pd.notna(latest["price"]):
    current_price = float(latest["price"])

projected_info = None
if forward_base_eok and forward_base_eok > 0:
    current_mcap_eok = latest["market_cap"] / 100_000_000
    if expected_price and expected_price > 0 and current_price and current_price > 0:
        projected_mcap_eok = current_mcap_eok * (expected_price / current_price)
        projected_price = expected_price
    else:
        projected_mcap_eok = expected_mcap_eok if expected_mcap_eok and expected_mcap_eok > 0 else current_mcap_eok
        projected_price = current_price * (projected_mcap_eok / current_mcap_eok) if current_price and current_mcap_eok > 0 else None
    projected_info = {
        "year": int(forward_year),
        "date": pd.Timestamp(year=int(forward_year), month=12, day=31),
        "base_eok": float(forward_base_eok),
        "mcap_eok": float(projected_mcap_eok),
        "price": float(projected_price) if projected_price else None,
        "multiple": float(projected_mcap_eok / forward_base_eok),
    }

st.subheader(f"{name} ({ticker})")
base_label = {"POR": "영업이익", "PER": "당기순이익", "PBR": "자본총계"}[valuation_metric]
latest_revenue_df = fin_df.dropna(subset=["revenue"]).tail(1)
latest_revenue = latest_revenue_df.iloc[0]["revenue"] if not latest_revenue_df.empty else None

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("현재가", f"{current_price:,.0f}원" if current_price else "-")
c2.metric(f"현재 {valuation_metric}", f"{latest['ratio']:.2f}배")
c3.metric("현재 시가총액", f"{latest['market_cap'] / 100_000_000:,.0f}억")
c4.metric(f"적용 {base_label}", f"{latest['base_value'] / 100_000_000:,.1f}억")
c5.metric("최근 매출액", f"{latest_revenue / 100_000_000:,.1f}억" if latest_revenue else "-")

fig, mean, std, n_points = plot_valuation(val_df, name, valuation_metric, chart_range, projected_info)
st.plotly_chart(fig, use_container_width=True)

s1, s2, s3 = st.columns(3)
s1.metric(f"{chart_range} 평균 {valuation_metric}", f"{mean:.2f}배")
s2.metric("표준편차", f"{std:.2f}")
s3.metric("차트 데이터 수", f"{n_points:,}개")

st.markdown("### 목표 배수 계산")
calc_base_eok = latest["base_value"] / 100_000_000
current_mcap_eok = latest["market_cap"] / 100_000_000
target_mcap_eok = target_multiple * calc_base_eok
target_price = current_price * (target_mcap_eok / current_mcap_eok) if current_price and current_mcap_eok > 0 else None
upside = (target_price / current_price - 1) * 100 if target_price and current_price else None

t1, t2, t3 = st.columns(3)
t1.metric("목표 시가총액", f"{target_mcap_eok:,.0f}억")
t2.metric("목표가", f"{target_price:,.0f}원" if target_price else "-")
t3.metric("상승여력", f"{upside:,.1f}%" if upside is not None else "-")

with st.expander("재무 원본 데이터", expanded=False):
    show_fin = fin_df.copy()
    for col in ["revenue", "operating_income", "net_income", "equity"]:
        if col in show_fin.columns:
            show_fin[col + "_억원"] = (show_fin[col] / 100_000_000).round(1)
    st.dataframe(show_fin, use_container_width=True)

with st.expander("시가총액 데이터", expanded=False):
    st.dataframe(mcap_df.tail(50), use_container_width=True)
