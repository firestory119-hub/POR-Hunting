import json
import os
import time
import urllib.error
import urllib.request
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

GITHUB_OWNER = "firestory119-hub"
GITHUB_REPO = "POR-Hunting"
GITHUB_WORKFLOW = "update_one_daily.yml"
GITHUB_BULK_WORKFLOW = "update_bulk_daily.yml"
MAX_BULK_ITEMS = 100

AUTO_REFRESH_SECONDS = 12
AUTO_REFRESH_MAX_TRIES = 20


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


def query_value(key, default=""):
    try:
        value = st.query_params.get(key, default)
        if isinstance(value, list):
            return str(value[0]) if value else default
        return str(value)
    except Exception:
        return default


def start_polling(ticker, name):
    st.query_params["scanner_collecting"] = str(ticker).zfill(6)
    st.query_params["scanner_collecting_name"] = str(name)
    st.query_params["scanner_poll_try"] = "0"


def stop_polling():
    for key in (
        "scanner_collecting",
        "scanner_collecting_name",
        "scanner_poll_try",
    ):
        try:
            del st.query_params[key]
        except Exception:
            pass


def request_collection(ticker, name):
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
            "User-Agent": "POR-Hunting-Scanner",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 204:
                return True, "수집을 요청했습니다."
            return False, f"GitHub 응답 코드: {response.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        return False, f"GitHub 요청 실패({exc.code}): {detail}"
    except Exception as exc:
        return False, f"GitHub 요청 실패: {exc}"



def request_bulk_collection(items):
    try:
        token = str(st.secrets["GITHUB_TOKEN"]).strip()
    except Exception:
        return False, "Streamlit Secrets에 GITHUB_TOKEN이 없습니다."

    if not token:
        return False, "Streamlit Secrets의 GITHUB_TOKEN이 비어 있습니다."

    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/actions/workflows/{GITHUB_BULK_WORKFLOW}/dispatches"
    )

    payload = json.dumps(
        {
            "ref": "main",
            "inputs": {
                "items_json": json.dumps(
                    items,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            },
        },
        ensure_ascii=False,
    ).encode("utf-8")

    request = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "POR-Hunting-Bulk-Scanner",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            if response.status == 204:
                return True, "일괄 수집을 요청했습니다."
            return False, f"GitHub 응답 코드: {response.status}"
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")[:300]
        return False, f"GitHub 요청 실패({exc.code}): {detail}"
    except Exception as exc:
        return False, f"GitHub 요청 실패: {exc}"


def parse_bulk_input(text, market):
    tokens = []

    for line in str(text).replace(",", "\n").splitlines():
        token = line.strip()
        if token:
            tokens.append(token)

    resolved = []
    unresolved = []
    seen = set()

    for token in tokens:
        code = clean_ticker(token)

        if code and len("".join(ch for ch in token if ch.isdigit())) >= 5:
            matched = market[market["종목코드"] == code]
        else:
            matched = market[
                market["종목명"].astype(str).str.lower().eq(token.lower())
            ]

            if matched.empty:
                matched = market[
                    market["종목명"]
                    .astype(str)
                    .str.lower()
                    .str.contains(token.lower(), na=False)
                ]

        if matched.empty:
            unresolved.append(token)
            continue

        row = matched.iloc[0]
        ticker = str(row["종목코드"]).zfill(6)

        if ticker in seen:
            continue

        seen.add(ticker)
        resolved.append(
            {
                "ticker": ticker,
                "name": str(row["종목명"]),
            }
        )

    return resolved, unresolved


BULK_REFRESH_MAX_TRIES = 600


def start_bulk_polling(items):
    tickers = ",".join(str(item["ticker"]).zfill(6) for item in items)
    st.query_params["bulk_tickers"] = tickers
    st.query_params["bulk_poll_try"] = "0"


def stop_bulk_polling():
    for key in ("bulk_tickers", "bulk_poll_try"):
        try:
            del st.query_params[key]
        except Exception:
            pass


def auto_poll_bulk(history, financial):
    raw_tickers = query_value("bulk_tickers", "")
    tickers = [
        clean_ticker(value)
        for value in raw_tickers.split(",")
        if clean_ticker(value)
    ]

    if not tickers:
        return False

    market_now = load_market()
    name_map = {}
    if not market_now.empty:
        name_map = dict(
            zip(
                market_now["종목코드"].astype(str),
                market_now["종목명"].astype(str),
            )
        )

    remaining = [
        ticker
        for ticker in tickers
        if not is_collected(ticker, history, financial)
    ]
    completed = len(tickers) - len(remaining)

    if not remaining:
        stop_bulk_polling()
        st.cache_data.clear()
        st.success(
            f"선택한 {len(tickers)}개 종목의 일괄 수집이 모두 완료되었습니다."
        )
        return False

    try:
        attempt = int(query_value("bulk_poll_try", "0"))
    except Exception:
        attempt = 0

    if attempt >= BULK_REFRESH_MAX_TRIES:
        stop_bulk_polling()
        st.warning(
            "자동 확인 시간이 끝났습니다. Actions 결과를 확인한 뒤 "
            "데이터 다시 읽기를 눌러주세요."
        )
        return False

    attempt += 1
    st.query_params["bulk_poll_try"] = str(attempt)

    remaining_names = [
        name_map.get(ticker, ticker)
        for ticker in remaining[:5]
    ]
    names = ", ".join(remaining_names)
    if len(remaining) > 5:
        names += f" 외 {len(remaining) - 5}개"

    progress = int(completed / max(1, len(tickers)) * 100)

    st.info(
        f"100개 일괄 수집 진행 중 · 완료 {completed}/{len(tickers)}개 · "
        f"남은 {len(remaining)}개: {names} · "
        f"{AUTO_REFRESH_SECONDS}초마다 자동 확인"
    )
    st.progress(progress)

    time.sleep(AUTO_REFRESH_SECONDS)
    st.cache_data.clear()
    st.rerun()
    return True


@st.cache_data(show_spinner=False, ttl=600)
def load_history():
    if not HISTORY_FILE.exists():
        return pd.DataFrame()

    df = pd.read_csv(HISTORY_FILE, dtype={"ticker": str})

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

    df = pd.read_csv(FINANCIAL_FILE, dtype={"ticker": str})

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

    required = {"ticker", "year", "operating_income", "net_income", "equity"}
    if not required.issubset(df.columns):
        return pd.DataFrame()

    if "name" not in df.columns:
        df["name"] = df["ticker"]
    if "revenue" not in df.columns:
        df["revenue"] = np.nan

    df["ticker"] = df["ticker"].map(clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")

    for column in ["revenue", "operating_income", "net_income", "equity"]:
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

    df = pd.read_csv(MARKET_FILE, dtype=str)

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


def is_collected(ticker, history, financial):
    ticker = str(ticker).zfill(6)
    has_history = (
        not history.empty
        and ticker in set(history["ticker"].dropna())
    )
    has_financial = (
        not financial.empty
        and ticker in set(financial["ticker"].dropna())
    )
    return has_history and has_financial


def auto_poll(ticker, name, history, financial):
    collecting = query_value("scanner_collecting")
    if collecting != str(ticker).zfill(6):
        return False

    if is_collected(ticker, history, financial):
        stop_polling()
        st.cache_data.clear()
        st.success(f"{name} 수집이 완료되었습니다.")
        return False

    try:
        attempt = int(query_value("scanner_poll_try", "0"))
    except Exception:
        attempt = 0

    if attempt >= AUTO_REFRESH_MAX_TRIES:
        stop_polling()
        st.warning(
            "자동 확인 시간이 끝났습니다. Actions 결과를 확인한 뒤 "
            "데이터 다시 읽기를 눌러주세요."
        )
        return False

    next_attempt = attempt + 1
    st.query_params["scanner_poll_try"] = str(next_attempt)

    st.info(
        f"{name} 데이터를 수집 중입니다. "
        f"{AUTO_REFRESH_SECONDS}초마다 자동 확인합니다. "
        f"({next_attempt}/{AUTO_REFRESH_MAX_TRIES})"
    )
    st.progress(
        min(100, int(next_attempt / AUTO_REFRESH_MAX_TRIES * 100))
    )

    time.sleep(AUTO_REFRESH_SECONDS)
    st.cache_data.clear()
    st.rerun()
    return True


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
    previous_op = previous_f["operating_income"] if previous_f is not None else np.nan

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
        ["available_date", "operating_income", "net_income", "equity"]
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

    por_percentile = percentile_rank(daily_10y["POR"], current_por)
    average_por = daily_10y.loc[daily_10y["POR"] > 0, "POR"].mean()

    return {
        "종목명": str(latest_h.get("name", ticker)),
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
        row = build_one_stock(ticker, history, financial)
        if row:
            rows.append(row)

    result = pd.DataFrame(rows)

    if result.empty:
        return result

    market = load_market()
    if not market.empty and "현재시총_억원" in market.columns:
        market_small = market[
            ["종목코드", "종목명", "현재시총_억원"]
        ].copy()

        result = result.merge(
            market_small,
            on="종목코드",
            how="left",
            suffixes=("", "_시장"),
        )

        result["종목명"] = result["종목명_시장"].fillna(result["종목명"])
        result["시가총액_억원"] = result["현재시총_억원"].fillna(
            result["시가총액_억원"]
        )

        result = result.drop(
            columns=["종목명_시장", "현재시총_억원"],
            errors="ignore",
        )

    return result


st.title("🔎 POR Scanner")
st.caption(
    "수집 종목을 스캔하고, 아직 수집되지 않은 종목은 이 화면에서 바로 추가합니다."
)

history = load_history()
financial = load_financial()
market = load_market()

with st.expander("🚀 여러 종목 일괄 자동 수집", expanded=True):
    if market.empty:
        st.warning("market_data.csv를 읽지 못했습니다.")
    else:
        bulk_text = st.text_area(
            "종목명 또는 종목코드 여러 개 입력",
            placeholder=(
                "싸이맥스\n코미코\n네오셈\n"
                "또는 싸이맥스, 코미코, 네오셈"
            ),
            height=150,
            key="scanner_bulk_text",
        )

        resolved_items, unresolved_items = parse_bulk_input(
            bulk_text,
            market,
        )

        collected_items = [
            item
            for item in resolved_items
            if is_collected(item["ticker"], history, financial)
        ]
        new_items = [
            item
            for item in resolved_items
            if not is_collected(item["ticker"], history, financial)
        ]

        if resolved_items:
            st.caption(
                f"인식 {len(resolved_items)}개 · "
                f"이미 수집 {len(collected_items)}개 · "
                f"신규 {len(new_items)}개"
            )

        if unresolved_items:
            st.warning(
                "찾지 못한 입력: " + ", ".join(unresolved_items)
            )

        if len(new_items) > MAX_BULK_ITEMS:
            st.error(
                f"신규 종목은 한 번에 최대 {MAX_BULK_ITEMS}개까지 가능합니다."
            )

        if auto_poll_bulk(history, financial):
            st.stop()

        if new_items and len(new_items) <= MAX_BULK_ITEMS:
            preview = pd.DataFrame(new_items)
            preview.columns = ["종목코드", "종목명"]
            st.dataframe(
                preview[["종목명", "종목코드"]],
                hide_index=True,
                use_container_width=True,
            )

            if st.button(
                f"신규 {len(new_items)}개 종목 일괄 수집",
                type="primary",
                key="scanner_bulk_collect",
            ):
                ok, message = request_bulk_collection(new_items)

                if ok:
                    start_bulk_polling(new_items)
                    st.success(
                        message
                        + " 종목을 한 개씩 순서대로 처리하며 자동 확인합니다."
                    )
                    time.sleep(2)
                    st.rerun()
                else:
                    st.error(message)

        elif resolved_items and not new_items:
            st.success("입력한 종목은 모두 이미 수집되어 있습니다.")


with st.expander("➕ 새 종목 자동 수집", expanded=True):
    if market.empty:
        st.warning("market_data.csv를 읽지 못했습니다.")
    else:
        collecting_name = query_value("scanner_collecting_name", "")
        search_text = st.text_input(
            "종목명 또는 종목코드",
            value=collecting_name,
            placeholder="예: 싸이맥스 또는 160980",
            key="scanner_stock_search",
        )

        filtered_market = market.copy()

        if search_text.strip():
            keyword = search_text.strip().lower()
            filtered_market = filtered_market[
                filtered_market["종목명"].astype(str).str.lower().str.contains(
                    keyword, na=False
                )
                | filtered_market["종목코드"].astype(str).str.contains(
                    keyword, na=False
                )
            ]

        options = [
            f"{row['종목명']} ({row['종목코드']})"
            for _, row in filtered_market.head(100).iterrows()
        ]

        if options:
            selected = st.selectbox(
                "종목 선택",
                options,
                key="scanner_stock_select",
            )

            selected_name = selected.rsplit(" (", 1)[0]
            selected_ticker = selected.rsplit("(", 1)[1].rstrip(")")

            collected = is_collected(
                selected_ticker,
                history,
                financial,
            )

            if auto_poll(
                selected_ticker,
                selected_name,
                history,
                financial,
            ):
                st.stop()

            if collected:
                st.success(
                    f"{selected_name}은 이미 일별·재무 데이터가 수집되어 있습니다."
                )
            else:
                st.warning(
                    f"{selected_name}은 아직 스캐너 대상에 없습니다."
                )

                if st.button(
                    "이 종목 일별·재무 데이터 자동 수집",
                    type="primary",
                    key=f"scanner_collect_{selected_ticker}",
                ):
                    ok, message = request_collection(
                        selected_ticker,
                        selected_name,
                    )

                    if ok:
                        start_polling(
                            selected_ticker,
                            selected_name,
                        )
                        st.success(
                            message
                            + " 완료될 때까지 자동으로 새로고침합니다."
                        )
                        time.sleep(2)
                        st.rerun()
                    else:
                        st.error(message)
        else:
            st.info("검색 결과가 없습니다.")

scanner = build_scanner()

if scanner.empty:
    st.error(
        "스캐너 데이터가 없습니다. "
        "위 자동 수집에서 종목을 먼저 추가하세요."
    )
    st.stop()

with st.sidebar:
    st.header("스캔 조건")

    max_por_percentile = st.slider(
        "POR 백분위 상한",
        1,
        100,
        30,
        1,
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

    if st.button(
        "데이터 다시 읽기",
        use_container_width=True,
    ):
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
    f"{filtered['POR백분위'].median():.1f}%"
    if not filtered.empty
    else "-",
)
c4.metric(
    "중앙 영업이익 증가율",
    f"{filtered['영업이익증가율'].median():.1f}%"
    if not filtered.empty
    else "-",
)

st.subheader("조건 통과 종목")

if filtered.empty:
    st.info(
        "현재 조건을 만족하는 종목이 없습니다. "
        "사이드바 조건을 완화해 보세요."
    )
else:
    display = filtered.copy()
    display["기준일"] = pd.to_datetime(
        display["기준일"]
    ).dt.strftime("%Y-%m-%d")

    numeric_columns = [
        "현재가",
        "시가총액_억원",
        "현재POR",
        "현재PER",
        "현재PBR",
        "10년평균POR",
        "POR백분위",
        "최근영업이익_억원",
        "영업이익증가율",
    ]

    for column in numeric_columns:
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
