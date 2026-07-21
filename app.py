import os
import csv
import json
import urllib.request
import urllib.error
import re
import time
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st




# =========================
# 기본 설정
# =========================
st.set_page_config(page_title="POR Hunting Pro v40.2 Callback Reset", layout="wide")

DATA_DIR = "data"
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")
API_KEY_FILE = os.path.join(DATA_DIR, "dart_api_key.txt")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
HISTORY_FILE = os.path.join(DATA_DIR, "search_history.csv")
MARKET_DATA_CSV = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_DATA_CSV = os.path.join(DATA_DIR, "financial_data.csv")
QUARTERLY_DATA_CSV = os.path.join(DATA_DIR, "financial_quarterly.csv")
MARKET_HISTORY_CSV = os.path.join(DATA_DIR, "market_history.csv")
CONSENSUS_XLSX = os.path.join(DATA_DIR, "consensus.xlsx")
GITHUB_OWNER = "firestory119-hub"
GITHUB_REPO = "POR-Hunting"
GITHUB_WORKFLOW = "update_one_daily.yml"
AUTO_REFRESH_SECONDS = 12
AUTO_REFRESH_MAX_TRIES = 20

os.makedirs(DATA_DIR, exist_ok=True)



st.title("POR Hunting Pro v40.2 Callback Reset")
st.caption("즐겨찾기 원키 넘기기 + 콜백 기반 예상값 완전 초기화")


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
    """
    CSV는 초기값만 읽고, 이후 변경은 세션 메모리에만 저장합니다.
    Streamlit Cloud에서 실행 중 파일을 수정하면 자동 재실행 루프가 생길 수 있습니다.
    """
    state_key = f"_session_{os.path.basename(path)}"

    if state_key not in st.session_state:
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, dtype=str)
            except Exception:
                df = pd.DataFrame(columns=columns)
        else:
            df = pd.DataFrame(columns=columns)

        for c in columns:
            if c not in df.columns:
                df[c] = ""

        st.session_state[state_key] = df[columns].drop_duplicates().copy()

    df = st.session_state[state_key].copy()
    for c in columns:
        if c not in df.columns:
            df[c] = ""

    return df[columns].drop_duplicates().copy()


def save_list_csv(df: pd.DataFrame, path: str):
    """
    저장소 파일에는 쓰지 않고 현재 세션에만 저장합니다.
    """
    state_key = f"_session_{os.path.basename(path)}"
    st.session_state[state_key] = df.drop_duplicates().copy()


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
    """
    같은 세션에서 같은 종목은 한 번만 최근검색에 추가합니다.
    """
    guard_key = f"_history_added_{ticker}"
    if st.session_state.get(guard_key):
        return

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
    st.session_state[guard_key] = True


# =========================
# 데이터 수집 함수 - CSV 전용
# =========================
@st.cache_data(show_spinner=False, ttl=3600)
def _load_market_csv() -> pd.DataFrame:
    if not os.path.exists(MARKET_DATA_CSV):
        raise RuntimeError("data/market_data.csv를 찾지 못했습니다.")

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

    required = {"종목명", "종목코드"}
    if not required.issubset(df.columns):
        raise RuntimeError("market_data.csv에 종목명/종목코드 열이 없습니다.")

    df["종목코드"] = (
        df["종목코드"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )

    for col in ["현재가", "현재시총_억원", "시가총액", "상장주식수", "PER", "PBR", "배당수익률"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    if "현재시총_억원" not in df.columns and "시가총액" in df.columns:
        df["현재시총_억원"] = df["시가총액"] / 100_000_000

    return df.drop_duplicates("종목코드").reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=3600)
def get_corp_codes(api_key: str = "") -> pd.DataFrame:
    """
    기존 UI 호환용 종목 목록.
    DART를 호출하지 않고 market_data.csv에서 종목명/코드를 구성합니다.
    """
    market = _load_market_csv()
    out = market[["종목명", "종목코드"]].copy()
    out.columns = ["corp_name", "stock_code"]
    out["corp_code"] = ""
    out["modify_date"] = ""
    return out[["corp_code", "corp_name", "stock_code", "modify_date"]]


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_financials(ticker: str, start_year: int, end_year: int) -> pd.DataFrame:
    """
    data/financial_data.csv에서 연간 재무를 읽습니다.
    """
    cols = [
        "year", "revenue", "operating_income", "net_income", "equity",
        "operating_margin", "revenue_account_nm", "op_account_nm",
        "net_account_nm", "equity_account_nm", "fs_div"
    ]

    if not os.path.exists(FINANCIAL_DATA_CSV):
        raise RuntimeError("data/financial_data.csv를 찾지 못했습니다.")

    df = pd.read_csv(FINANCIAL_DATA_CSV, dtype={"ticker": str})

    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})
    if "종목명" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"종목명": "name"})

    if "ticker" not in df.columns or "year" not in df.columns:
        raise RuntimeError("financial_data.csv에 ticker/year 열이 없습니다.")

    df["ticker"] = (
        df["ticker"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for col in ["revenue", "operating_income", "net_income", "equity", "operating_margin"]:
        if col not in df.columns:
            df[col] = None
        df[col] = pd.to_numeric(df[col], errors="coerce")

    for col in cols:
        if col not in df.columns:
            df[col] = None

    out = df[
        (df["ticker"] == str(ticker).zfill(6))
        & (df["year"] >= int(start_year))
        & (df["year"] <= int(end_year))
    ][cols].copy()

    return out.sort_values("year").reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=3600)
def _load_quarterly_for_ticker(ticker: str, start_year: int, end_year: int) -> pd.DataFrame:
    if not os.path.exists(QUARTERLY_DATA_CSV):
        return pd.DataFrame()

    try:
        df = pd.read_csv(QUARTERLY_DATA_CSV, dtype={"ticker": str})
    except Exception:
        return pd.DataFrame()

    if "ticker" not in df.columns:
        return pd.DataFrame()

    df["ticker"] = (
        df["ticker"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )
    df["year"] = pd.to_numeric(df.get("year"), errors="coerce")
    df["quarter"] = pd.to_numeric(df.get("quarter"), errors="coerce")
    df["period_end"] = pd.to_datetime(df.get("period_end"), errors="coerce")

    return df[
        (df["ticker"] == str(ticker).zfill(6))
        & (df["year"] >= int(start_year))
        & (df["year"] <= int(end_year))
    ].sort_values(["year", "quarter"])


@st.cache_data(show_spinner=False, ttl=3600)
def fetch_market_cap(
    ticker: str,
    start_date: str,
    end_date: str,
    chart_mode: str = "분기",
) -> pd.DataFrame:
    """
    차트 기준에 따라 시가총액 데이터를 구성합니다.

    연도:
      현재 시가총액/현재가를 연말 시점에 배치합니다.

    분기:
      financial_quarterly.csv의 분기말 시점에 배치합니다.
      분기 데이터가 없으면 연도 시점으로 자동 대체합니다.

    일별:
      data/market_history.csv에서 선택 종목의 일별 주가/시가총액만
      한 줄씩 읽어 메모리 사용을 최소화합니다.
    """
    target = str(ticker).zfill(6)
    start_ts = pd.to_datetime(start_date, format="%Y%m%d", errors="coerce")
    end_ts = pd.to_datetime(end_date, format="%Y%m%d", errors="coerce")

    if chart_mode == "일별":
        columns = ["date", "market_cap", "price"]

        if not os.path.exists(MARKET_HISTORY_CSV):
            return pd.DataFrame(columns=columns)

        rows = []

        try:
            with open(
                MARKET_HISTORY_CSV,
                "r",
                encoding="utf-8-sig",
                newline="",
            ) as handle:
                reader = csv.DictReader(handle)

                required = {"ticker", "date", "price", "market_cap"}
                if not required.issubset(set(reader.fieldnames or [])):
                    return pd.DataFrame(columns=columns)

                for item in reader:
                    code = (
                        str(item.get("ticker", ""))
                        .strip()
                        .replace(".0", "")
                        .zfill(6)
                    )

                    if code != target:
                        continue

                    date_value = pd.to_datetime(
                        item.get("date"),
                        errors="coerce",
                    )
                    price_value = clean_num(item.get("price"))
                    market_cap_value = clean_num(item.get("market_cap"))

                    if (
                        pd.isna(date_value)
                        or market_cap_value is None
                        or date_value < start_ts
                        or date_value > end_ts
                    ):
                        continue

                    rows.append(
                        {
                            "date": date_value,
                            "market_cap": market_cap_value,
                            "price": price_value,
                        }
                    )
        except Exception:
            return pd.DataFrame(columns=columns)

        if not rows:
            return pd.DataFrame(columns=columns)

        return (
            pd.DataFrame(rows)
            .drop_duplicates("date", keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

    market = _load_market_csv()
    row = market[market["종목코드"] == target]

    if row.empty:
        return pd.DataFrame()

    current_price = clean_num(row.iloc[0].get("현재가"))
    current_mcap_eok = clean_num(row.iloc[0].get("현재시총_억원"))

    if not current_mcap_eok or current_mcap_eok <= 0:
        return pd.DataFrame()

    start_year = int(start_date[:4])
    end_year = int(end_date[:4])
    dates = []

    if chart_mode == "분기":
        quarterly = _load_quarterly_for_ticker(
            ticker,
            start_year,
            end_year,
        )

        if not quarterly.empty and quarterly["period_end"].notna().any():
            dates = (
                quarterly["period_end"]
                .dropna()
                .drop_duplicates()
                .tolist()
            )

    if not dates:
        dates = [
            pd.Timestamp(year=year, month=12, day=31)
            for year in range(start_year, end_year + 1)
        ]

    return pd.DataFrame(
        {
            "date": dates,
            "market_cap": [
                float(current_mcap_eok) * 100_000_000
            ] * len(dates),
            "price": [
                float(current_price) if current_price else None
            ] * len(dates),
        }
    )


@st.cache_data(show_spinner=False, ttl=1800)
def get_current_price(ticker: str):
    market = _load_market_csv()
    row = market[market["종목코드"] == str(ticker).zfill(6)]
    if row.empty:
        return None

    value = clean_num(row.iloc[0].get("현재가"))
    return value if value and value > 0 else None




@st.cache_data(show_spinner=False, ttl=300)
def load_consensus_excel() -> pd.DataFrame:
    columns = [
        "name", "ticker", "year", "operating_income_eok",
        "target_por", "source", "updated_at", "note",
    ]

    if not os.path.exists(CONSENSUS_XLSX):
        return pd.DataFrame(columns=columns)

    try:
        wide = pd.read_excel(
            CONSENSUS_XLSX,
            sheet_name="컨센서스입력",
            header=1,
            dtype={"종목코드": str},
        )
    except Exception:
        return pd.DataFrame(columns=columns)

    required = {"종목명", "종목코드"}
    if not required.issubset(wide.columns):
        return pd.DataFrame(columns=columns)

    year_cols = [
        col for col in wide.columns
        if re.fullmatch(r"\d{4}E?", str(col).strip())
    ]
    if not year_cols:
        return pd.DataFrame(columns=columns)

    id_cols = [
        col for col in [
            "종목명", "종목코드", "목표POR",
            "출처", "업데이트일", "비고",
        ]
        if col in wide.columns
    ]

    long_df = wide.melt(
        id_vars=id_cols,
        value_vars=year_cols,
        var_name="year",
        value_name="operating_income_eok",
    )

    long_df = long_df.rename(columns={
        "종목명": "name",
        "종목코드": "ticker",
        "목표POR": "target_por",
        "출처": "source",
        "업데이트일": "updated_at",
        "비고": "note",
    })

    long_df["ticker"] = (
        long_df["ticker"].astype(str)
        .str.replace(r"\.0$", "", regex=True)
        .str.zfill(6)
    )
    long_df["year"] = pd.to_numeric(
        long_df["year"].astype(str).str.extract(r"(\d{4})")[0],
        errors="coerce",
    )
    long_df["operating_income_eok"] = pd.to_numeric(
        long_df["operating_income_eok"], errors="coerce"
    )
    long_df["target_por"] = pd.to_numeric(
        long_df.get("target_por"), errors="coerce"
    )

    return (
        long_df.dropna(
            subset=["ticker", "year", "operating_income_eok"]
        )
        .sort_values(["ticker", "year"])
        .reset_index(drop=True)
    )


def get_consensus_for_ticker(ticker: str) -> pd.DataFrame:
    df = load_consensus_excel()
    if df.empty:
        return df

    return (
        df[df["ticker"] == str(ticker).zfill(6)]
        .copy()
        .sort_values("year")
        .reset_index(drop=True)
    )


def request_daily_collection(ticker: str, name: str) -> tuple[bool, str]:
    """
    Streamlit Secrets의 GITHUB_TOKEN으로 GitHub Actions를 실행합니다.
    """
    try:
        token = str(st.secrets["GITHUB_TOKEN"]).strip()
    except Exception:
        return False, "Streamlit Secrets에 GITHUB_TOKEN이 없습니다."

    if not token:
        return False, "Streamlit Secrets의 GITHUB_TOKEN이 비어 있습니다."

    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/{GITHUB_WORKFLOW}/dispatches"
    )

    payload = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "ticker": str(ticker).zfill(6),
                "name": str(name),
            },
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "POR-Hunting-Pro",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 204:
                return True, "일별 데이터 수집을 요청했습니다."
            return False, f"GitHub 응답 코드: {response.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        return False, f"GitHub 요청 실패({exc.code}): {detail}"
    except Exception as exc:
        return False, f"GitHub 요청 실패: {exc}"


def _query_value(key: str, default: str = "") -> str:
    try:
        value = st.query_params.get(key, default)
        if isinstance(value, list):
            return str(value[0]) if value else default
        return str(value)
    except Exception:
        return default


def start_auto_collection(ticker: str, name: str):
    st.query_params["collecting"] = str(ticker).zfill(6)
    st.query_params["collecting_name"] = str(name)
    st.query_params["poll_try"] = "0"


def stop_auto_collection():
    for key in ("collecting", "collecting_name", "poll_try"):
        try:
            del st.query_params[key]
        except Exception:
            pass


def auto_poll_collection(ticker: str, name: str):
    collecting = _query_value("collecting")
    if collecting != str(ticker).zfill(6):
        return False

    try:
        attempt = int(_query_value("poll_try", "0"))
    except Exception:
        attempt = 0

    if attempt >= AUTO_REFRESH_MAX_TRIES:
        st.warning(
            "자동 확인 시간이 끝났습니다. GitHub Actions 결과를 확인한 뒤 "
            "새로고침해 주세요."
        )
        stop_auto_collection()
        return False

    next_attempt = attempt + 1
    st.query_params["poll_try"] = str(next_attempt)

    remaining = AUTO_REFRESH_MAX_TRIES - next_attempt
    st.info(
        f"{name} 데이터를 수집 중입니다. "
        f"{AUTO_REFRESH_SECONDS}초마다 자동 확인합니다. "
        f"(확인 {next_attempt}/{AUTO_REFRESH_MAX_TRIES}, 남은 {remaining}회)"
    )

    progress = min(
        100,
        int(next_attempt / AUTO_REFRESH_MAX_TRIES * 100),
    )
    st.progress(progress)

    time.sleep(AUTO_REFRESH_SECONDS)

    try:
        fetch_market_cap.clear()
        fetch_financials.clear()
        _load_market_csv.clear()
    except Exception:
        pass

    st.rerun()
    return True

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
    latest_available_year = {}

    for y in sorted(out["year"].unique()):
        candidates = [
            yy
            for yy, vals in fin_map.items()
            if yy <= y
            and vals.get(base_col) is not None
            and pd.notna(vals.get(base_col))
            and vals.get(base_col) > 0
        ]

        if candidates:
            selected_year = max(candidates)
            latest_available[y] = fin_map[selected_year].get(base_col)
            latest_available_year[y] = selected_year
        else:
            latest_available[y] = None
            latest_available_year[y] = None

    out["base_value"] = out["year"].map(latest_available)
    out["base_year"] = out["year"].map(latest_available_year)
    out["base_source"] = "financial_data.csv"

    if forward_year and forward_oi_eok and forward_oi_eok > 0:
        forward_mask = out["year"] >= int(forward_year)
        out.loc[forward_mask, "base_value"] = forward_oi_eok * 100_000_000
        out.loc[forward_mask, "base_year"] = int(forward_year)
        out.loc[forward_mask, "base_source"] = "예상 입력/컨센서스"

    out = out.dropna(subset=["base_value"])
    out = out[out["base_value"] > 0]
    out[metric] = out["market_cap"] / out["base_value"]
    out["ratio"] = out[metric]

    out = out[out["ratio"] > 0]

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


def reset_manual_projection_inputs():
    """종목 변경 전에 수동 예상 입력 위젯 상태를 안전하게 삭제합니다."""
    for key in (
        "forward_year_input",
        "forward_oi_input",
        "expected_mcap_input",
        "expected_price_input",
    ):
        st.session_state.pop(key, None)


def navigate_favorite(direction: int, favorite_rows: list[dict]):
    """
    Streamlit 버튼 콜백.
    콜백은 새 화면을 그리기 전에 실행되므로 예상 입력값이 확실히 초기화됩니다.
    """
    if not favorite_rows:
        return

    count = len(favorite_rows)
    current_index = int(
        st.session_state.get("favorite_nav_index", 0)
    )
    next_index = (current_index + int(direction)) % count
    selected = favorite_rows[next_index]

    reset_manual_projection_inputs()

    st.session_state["favorite_nav_index"] = next_index
    st.session_state["stock_query"] = selected["name"]
    st.session_state["_favorite_nav_ticker"] = selected["ticker"]

    # 빠른 선택 드롭다운과 충돌하지 않도록 직접 입력으로 되돌립니다.
    st.session_state["quick_stock_select"] = "직접 입력"
    st.session_state["_last_quick_selection"] = "직접 입력"


def apply_quick_selection(quick_map: dict):
    """빠른 선택 변경 콜백."""
    selected = st.session_state.get(
        "quick_stock_select",
        "직접 입력",
    )

    if selected == "직접 입력":
        return

    selected_name = quick_map.get(selected)
    if not selected_name:
        return

    reset_manual_projection_inputs()
    st.session_state["stock_query"] = selected_name
    st.session_state["_last_quick_selection"] = selected


def on_stock_query_change():
    """검색창에서 다른 종목을 입력할 때 수동 예상값을 초기화합니다."""
    current_query = str(
        st.session_state.get("stock_query", "")
    ).strip()

    previous_query = str(
        st.session_state.get("_last_stock_query", "")
    ).strip()

    if previous_query and current_query != previous_query:
        reset_manual_projection_inputs()

    st.session_state["_last_stock_query"] = current_query



# =========================
# 사이드바
# =========================
with st.sidebar:
    st.header("설정")

    saved_key = ""
    api_key = st.text_input(
        "OpenDART API Key (CSV 모드에서는 불필요)",
        value="",
        type="password"
    )


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
        on_change=apply_quick_selection,
        args=(quick_map,),
    )

    # V39: 즐겨찾기 원키 넘기기
    favorite_rows = []

    if not fav_df_side.empty:
        favorite_rows = (
            fav_df_side
            .sort_values("saved_at", ascending=False)
            .drop_duplicates(subset=["ticker"], keep="first")
            [["name", "ticker"]]
            .to_dict("records")
        )

    if favorite_rows:
        favorite_count = len(favorite_rows)

        current_favorite_index = int(
            st.session_state.get("favorite_nav_index", 0)
        )
        current_favorite_index = max(
            0,
            min(current_favorite_index, favorite_count - 1),
        )
        st.session_state["favorite_nav_index"] = (
            current_favorite_index
        )

        nav_prev, nav_count, nav_next = st.columns(
            [1, 1.2, 1]
        )

        with nav_prev:
            st.button(
                "◀ 이전",
                use_container_width=True,
                key="favorite_prev_button",
                on_click=navigate_favorite,
                args=(-1, favorite_rows),
            )

        with nav_count:
            st.markdown(
                (
                    "<div style='text-align:center;"
                    "padding-top:0.45rem;font-weight:700;'>"
                    f"{current_favorite_index + 1} / "
                    f"{favorite_count}"
                    "</div>"
                ),
                unsafe_allow_html=True,
            )

        with nav_next:
            st.button(
                "다음 ▶",
                use_container_width=True,
                type="primary",
                key="favorite_next_button",
                on_click=navigate_favorite,
                args=(1, favorite_rows),
            )

        current_favorite = favorite_rows[
            st.session_state["favorite_nav_index"]
        ]
        st.caption(
            "현재 순서: "
            f"{current_favorite['name']} "
            f"({current_favorite['ticker']})"
        )
    else:
        st.caption(
            "즐겨찾기를 추가하면 ◀ 이전 / 다음 ▶ 버튼으로 "
            "차트를 바로 넘길 수 있습니다."
        )

    st.divider()

    valuation_metric = st.radio(
        "밴드 지표",
        ["POR", "PER", "PBR"],
        index=0,
        horizontal=True,
    )

    chart_mode = st.radio(
        "차트 기준",
        ["연도", "분기", "일별"],
        index=2,
        horizontal=True,
        key="chart_mode",
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
        key="forward_year_input",
    )

    if valuation_metric == "POR":
        expected_base_label = "예상 영업이익"
    elif valuation_metric == "PER":
        expected_base_label = "예상 당기순이익"
    else:
        expected_base_label = "예상 자본총계"

    forward_oi_eok = st.number_input(
        f"{expected_base_label}(억원, 선택)",
        value=0.0,
        step=10.0,
        key="forward_oi_input",
    )
    expected_mcap_eok = st.number_input(
        "예상 시가총액(억원, 선택)",
        value=0.0,
        step=50.0,
        key="expected_mcap_input",
    )
    expected_price = st.number_input(
        "예상 주가(원, 선택)",
        value=0.0,
        step=100.0,
        key="expected_price_input",
    )
    target_por_slider = st.slider(f"목표 {valuation_metric}", 1.0, 30.0, 8.0, 0.5)
    bear_por = st.number_input("보수 POR", value=5.0, step=0.5)
    base_por = st.number_input("적정 POR", value=8.0, step=0.5)
    bull_por = st.number_input("낙관 POR", value=12.0, step=0.5)
    target_multiple_manual = st.number_input("목표 배수 직접입력(선택)", value=0.0, step=0.5)

    st.caption("v25: 미래 POR, 적정가, 텐베거, 간단 리포트까지 한 번에 확인합니다.")


# =========================
# 메인 화면
# =========================
default_query = _query_value(
    "collecting_name",
    "삼성전자",
)

if "stock_query" not in st.session_state:
    st.session_state["stock_query"] = default_query

query = st.text_input(
    "Stock Name",
    key="stock_query",
    on_change=on_stock_query_change,
    help=(
        "종목명을 입력하고 엔터를 누르면 자동으로 조회됩니다. "
        "즐겨찾기는 사이드바의 ◀ 이전 / 다음 ▶ 버튼으로 "
        "즉시 넘길 수 있습니다."
    ),
)

if "_last_stock_query" not in st.session_state:
    st.session_state["_last_stock_query"] = query

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
    corp_code = ""
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

    end_year = datetime.today().year - 1  # 완료된 사업보고서 기준
    start_year = end_year - years + 1
    start_date = f"{start_year}0101"
    end_date = datetime.today().strftime("%Y%m%d")

    with st.spinner("저장된 재무 데이터를 불러오는 중..."):
        try:
            fin_df = fetch_financials(ticker, start_year, end_year)
        except Exception as e:
            st.error(f"재무 데이터 읽기 실패: {e}")
            st.stop()

    with st.spinner("저장된 시가총액 데이터를 불러오는 중..."):
        try:
            mcap_df = fetch_market_cap(
                ticker,
                start_date,
                end_date,
                chart_mode,
            )
        except Exception as e:
            st.error(f"시가총액 수집 실패: {e}")
            st.stop()

    if mcap_df.empty:
        if chart_mode == "일별":
            st.warning(f"{name}의 일별 데이터가 아직 없습니다.")

            if auto_poll_collection(ticker, name):
                st.stop()

            if st.button(
                "이 종목 일별·재무 데이터 수집 요청",
                type="primary",
                key=f"request_daily_{ticker}",
            ):
                ok, message = request_daily_collection(ticker, name)
                if ok:
                    start_auto_collection(ticker, name)
                    st.success(
                        message
                        + " 이제 화면이 자동으로 새로고침되며 완료 여부를 확인합니다."
                    )
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(message)

            st.caption(
                "버튼은 종목별 최초 1회만 누르면 됩니다. "
                "GitHub Actions 완료 후 차트가 자동으로 표시됩니다."
            )
        else:
            st.error("시가총액 데이터를 가져오지 못했습니다.")
        st.stop()

    consensus_df = get_consensus_for_ticker(ticker)

    applied_forward_year = int(forward_year)
    applied_forward_oi_eok = (
        float(forward_oi_eok)
        if forward_oi_eok and forward_oi_eok > 0
        else None
    )

    if applied_forward_oi_eok is None and not consensus_df.empty:
        future_consensus = consensus_df[
            consensus_df["year"] >= datetime.today().year
        ]
        if future_consensus.empty:
            future_consensus = consensus_df.copy()

        first_consensus = future_consensus.iloc[0]
        applied_forward_year = int(first_consensus["year"])
        applied_forward_oi_eok = float(
            first_consensus["operating_income_eok"]
        )

    val_df = make_valuation_df(
        mcap_df,
        fin_df,
        valuation_metric,
        applied_forward_year,
        applied_forward_oi_eok,
    )

    if val_df.empty:
        if chart_mode == "일별":
            st.warning(
                f"{name}의 재무 데이터가 아직 없거나 "
                f"{valuation_metric} 계산 기준값이 준비되지 않았습니다."
            )

            if auto_poll_collection(ticker, name):
                st.stop()

            if fin_df.empty and st.button(
                "이 종목 일별·재무 데이터 수집 요청",
                type="primary",
                key=f"request_financial_{ticker}",
            ):
                ok, message = request_daily_collection(ticker, name)
                if ok:
                    start_auto_collection(ticker, name)
                    st.success(
                        message
                        + " 완료될 때까지 자동으로 확인합니다."
                    )
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(message)
        else:
            st.error(
                "밴드 계산이 불가능합니다. 선택 지표의 기준값이 없거나 "
                "적자/마이너스일 수 있습니다."
            )

        if not fin_df.empty:
            st.dataframe(fin_df)
        st.stop()

    if _query_value("collecting") == str(ticker).zfill(6):
        stop_auto_collection()
        st.success(f"{name} 데이터 수집이 완료되었습니다.")

    # v17.3: 미래 예상 POR 계산용 정보
    projected_info = None
    projected_multiple = None
    projected_mcap_eok = None

    if applied_forward_oi_eok and applied_forward_oi_eok > 0:
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

        projected_multiple = projected_mcap_eok / applied_forward_oi_eok

        projected_info = {
            "year": int(applied_forward_year),
            "date": pd.Timestamp(year=int(applied_forward_year), month=12, day=31),
            "oi_eok": float(applied_forward_oi_eok),
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

    # 실제 최근 연도 실적과 최근 흑자 기준을 분리해 표시합니다.
    actual_fin_sorted = fin_df.sort_values("year").copy()
    latest_actual_row = (
        actual_fin_sorted.tail(1).iloc[0]
        if not actual_fin_sorted.empty
        else None
    )

    latest_actual_year = (
        int(latest_actual_row["year"])
        if latest_actual_row is not None
        and pd.notna(latest_actual_row["year"])
        else None
    )
    latest_actual_oi = (
        float(latest_actual_row["operating_income"])
        if latest_actual_row is not None
        and pd.notna(latest_actual_row["operating_income"])
        else None
    )

    latest_base_year = (
        int(latest["base_year"])
        if "base_year" in latest.index
        and pd.notna(latest["base_year"])
        else None
    )
    latest_base_eok = latest["base_value"] / 100_000_000

    if valuation_metric == "POR" and latest_actual_oi is not None and latest_actual_oi <= 0:
        actual_por_display = "적자(N/A)"
    else:
        actual_por_display = f"{latest['ratio']:.2f}배"

    c1.metric("현재가", f"{current_price:,.0f}원" if current_price else "-")
    c2.metric(
        f"최근 흑자 POR ({latest_base_year})" if valuation_metric == "POR" and latest_base_year else f"현재 {valuation_metric}",
        f"{latest['ratio']:.2f}배",
    )
    c3.metric("현재 시가총액", f"{latest['market_cap'] / 100_000_000:,.0f}억")
    c4.metric(
        "최근 흑자 기준 영업이익" if valuation_metric == "POR" else "적용 기준값",
        f"{latest_base_eok:,.1f}억",
    )
    c5.metric("최근 매출액", f"{latest_revenue / 100_000_000:,.1f}억" if latest_revenue else "-")
    c6.metric("기준일", latest["date"].strftime("%Y-%m-%d"))
    c7.metric(
        f"{latest_actual_year} 실제 POR" if valuation_metric == "POR" and latest_actual_year else "차트 기준",
        actual_por_display if valuation_metric == "POR" else f"{chart_mode} / {chart_range}",
    )
    c8.metric(f"예상 {valuation_metric}", f"{projected_multiple:.2f}" if projected_multiple else "-")

    with st.expander("🔎 POR 계산 근거 보기", expanded=False):
        current_mcap_eok_debug = latest["market_cap"] / 100_000_000
        current_base_eok_debug = latest["base_value"] / 100_000_000
        current_base_year_debug = (
            int(latest["base_year"])
            if "base_year" in latest.index and pd.notna(latest["base_year"])
            else "-"
        )
        current_base_source_debug = (
            str(latest["base_source"])
            if "base_source" in latest.index and pd.notna(latest["base_source"])
            else "financial_data.csv"
        )

        st.markdown("#### 최근 흑자 기준 계산")
        d1, d2, d3, d4 = st.columns(4)
        d1.metric("현재 시가총액", f"{current_mcap_eok_debug:,.1f}억")
        d2.metric("사용 영업이익", f"{current_base_eok_debug:,.1f}억")
        d3.metric("사용 연도", str(current_base_year_debug))
        d4.metric("출처", current_base_source_debug)

        st.code(
            f"최근 흑자 POR = 현재 시가총액 ÷ 최근 흑자 영업이익\n"
            f"              = {current_mcap_eok_debug:,.1f}억 ÷ {current_base_eok_debug:,.1f}억\n"
            f"              = {latest['ratio']:.2f}배",
            language="text",
        )

        if latest_actual_year is not None and latest_actual_oi is not None:
            latest_actual_oi_eok = latest_actual_oi / 100_000_000
            if latest_actual_oi_eok <= 0:
                st.warning(
                    f"{latest_actual_year}년 실제 영업이익은 {latest_actual_oi_eok:,.1f}억으로 적자입니다. "
                    f"따라서 실제 POR는 N/A이며, 화면의 {latest['ratio']:.2f}배는 "
                    f"{current_base_year_debug}년 최근 흑자 실적을 사용한 값입니다."
                )

        if applied_forward_oi_eok and applied_forward_oi_eok > 0:
            expected_por_debug = current_mcap_eok_debug / applied_forward_oi_eok
            expected_source = (
                "사이드바 수동 입력"
                if forward_oi_eok and forward_oi_eok > 0
                else "consensus.xlsx"
            )
            st.markdown("#### 예상 영업이익 기준 계산")
            e1, e2, e3 = st.columns(3)
            e1.metric(f"{int(applied_forward_year)}E 영업이익", f"{applied_forward_oi_eok:,.1f}억")
            e2.metric("예상 POR", f"{expected_por_debug:.2f}배")
            e3.metric("예상값 출처", expected_source)
            st.code(
                f"{int(applied_forward_year)}E POR = 현재 시가총액 ÷ 예상 영업이익\n"
                f"                 = {current_mcap_eok_debug:,.1f}억 ÷ {applied_forward_oi_eok:,.1f}억\n"
                f"                 = {expected_por_debug:.2f}배",
                language="text",
            )

        actual_debug = fin_df[["year", "operating_income"]].copy()
        actual_debug["영업이익(억)"] = (
            pd.to_numeric(actual_debug["operating_income"], errors="coerce") / 100_000_000
        ).round(1)
        actual_debug["상태"] = actual_debug["영업이익(억)"].map(
            lambda value: "흑자" if pd.notna(value) and value > 0 else "적자" if pd.notna(value) else "-"
        )
        actual_debug = actual_debug[["year", "영업이익(억)", "상태"]].rename(columns={"year": "연도"})
        st.markdown("#### financial_data.csv 실제 영업이익")
        st.dataframe(
            actual_debug.sort_values("연도", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

    fig, mean, std, stat_count, stat_start_date, displayed_df = plot_valuation(
        val_df,
        f"{name} {chart_mode} Multiple",
        valuation_metric,
        chart_range,
        projected_info,
    )

    st.plotly_chart(
        fig,
        width="stretch",
        key=f"{ticker}_{valuation_metric}_{chart_mode}_{chart_range}_{forward_year}_{forward_oi_eok}_{expected_mcap_eok}_{expected_price}_{projected_multiple}"
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
            d1.metric(f"{int(forward_year)}E {expected_base_label}", f"{applied_forward_oi_eok:,.1f}억")
            d2.metric("예상 시가총액", f"{projected_mcap_eok:,.0f}억")
            d3.metric(f"현재 {valuation_metric} 대비", f"{(projected_multiple / latest['ratio'] - 1) * 100:.1f}%")

    if not consensus_df.empty:
        st.markdown("### 저장된 연도별 영업이익 컨센서스")

        consensus_show = consensus_df.copy()
        current_mcap_eok_consensus = latest["market_cap"] / 100_000_000

        consensus_show["현재 시총 기준 POR"] = (
            current_mcap_eok_consensus
            / consensus_show["operating_income_eok"]
        )

        consensus_show["적용 목표 POR"] = (
            consensus_show["target_por"]
            .where(
                consensus_show["target_por"] > 0,
                float(target_por_slider),
            )
        )

        consensus_show["목표 시가총액(억)"] = (
            consensus_show["operating_income_eok"]
            * consensus_show["적용 목표 POR"]
        )

        if current_price and current_mcap_eok_consensus > 0:
            consensus_show["목표 주가(원)"] = (
                current_price
                * consensus_show["목표 시가총액(억)"]
                / current_mcap_eok_consensus
            )
            consensus_show["상승여력(%)"] = (
                consensus_show["목표 주가(원)"]
                / current_price - 1
            ) * 100
        else:
            consensus_show["목표 주가(원)"] = None
            consensus_show["상승여력(%)"] = None

        consensus_show["연도"] = (
            consensus_show["year"].astype(int).astype(str) + "E"
        )
        consensus_show["예상 영업이익(억)"] = (
            consensus_show["operating_income_eok"].round(1)
        )
        consensus_show["현재 시총 기준 POR"] = (
            consensus_show["현재 시총 기준 POR"].round(2)
        )
        consensus_show["적용 목표 POR"] = (
            consensus_show["적용 목표 POR"].round(2)
        )
        consensus_show["목표 시가총액(억)"] = (
            consensus_show["목표 시가총액(억)"].round(1)
        )
        consensus_show["목표 주가(원)"] = (
            pd.to_numeric(
                consensus_show["목표 주가(원)"],
                errors="coerce",
            ).round(0)
        )
        consensus_show["상승여력(%)"] = (
            pd.to_numeric(
                consensus_show["상승여력(%)"],
                errors="coerce",
            ).round(1)
        )

        st.dataframe(
            consensus_show[
                [
                    "연도",
                    "예상 영업이익(억)",
                    "현재 시총 기준 POR",
                    "적용 목표 POR",
                    "목표 시가총액(억)",
                    "목표 주가(원)",
                    "상승여력(%)",
                    "source",
                    "updated_at",
                    "note",
                ]
            ].rename(columns={
                "source": "출처",
                "updated_at": "업데이트일",
                "note": "비고",
            }),
            use_container_width=True,
            hide_index=True,
        )

        st.caption(
            "사이드바 예상 영업이익을 직접 입력하면 수동값 우선. "
            "0이면 엑셀의 가장 가까운 미래 연도가 자동 적용됩니다."
        )

    # v20: 목표 POR 슬라이더 계산
    if applied_forward_oi_eok and applied_forward_oi_eok > 0:
        target_mcap_eok_by_slider = target_por_slider * applied_forward_oi_eok
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
            td2.metric("적용 영업이익", f"{applied_forward_oi_eok:,.1f}억")


    # v22: POR Calculator Pro
    if valuation_metric in ["POR", "PER", "PBR"]:
        st.markdown(f"### {valuation_metric} Calculator Pro")

        calc_base_eok = None
        calc_base_label = "현재 적용 기준값"
        if applied_forward_oi_eok and applied_forward_oi_eok > 0:
            calc_base_eok = float(applied_forward_oi_eok)
            calc_base_label = f"{int(applied_forward_year)}E {expected_base_label}"
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

            st.dataframe(styled, width="stretch", hide_index=True)

            st.caption("노란색=현재 POR, 파란색=선택 기간 평균 POR, 초록색=목표 POR입니다.")
        else:
            st.info(f"{valuation_metric} Calculator를 표시하려면 기준값 데이터가 필요합니다.")


    # v25: Fair Value / Tenbagger / Simple Report
    if valuation_metric == "POR":
        st.markdown("### 적정가 시나리오")

        scenario_base_eok = None
        if applied_forward_oi_eok and applied_forward_oi_eok > 0:
            scenario_base_eok = float(applied_forward_oi_eok)
            scenario_label = f"{int(applied_forward_year)}E {expected_base_label}"
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
                width="stretch",
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
        width="stretch",
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

    st.dataframe(show_targets, width="stretch")

    with st.expander("즐겨찾기 / 최근 검색 관리", expanded=False):
        fav_manage = load_list_csv(FAVORITES_FILE, ["name", "ticker", "saved_at"])
        hist_manage = load_list_csv(HISTORY_FILE, ["name", "ticker", "searched_at"])

        st.markdown("#### 즐겨찾기")
        st.dataframe(
            fav_manage.sort_values("saved_at", ascending=False) if not fav_manage.empty else fav_manage,
            width="stretch",
        )

        st.markdown("#### 최근 검색")
        st.dataframe(
            hist_manage.sort_values("searched_at", ascending=False) if not hist_manage.empty else hist_manage,
            width="stretch",
        )

else:
    st.info("종목명을 입력하면 저장된 CSV 데이터로 자동 조회됩니다.")