
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr


DATA_DIR = "data"
MARKET_FILE = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_FILE = os.path.join(DATA_DIR, "financial_data.csv")
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
SEARCH_HISTORY_FILE = os.path.join(DATA_DIR, "search_history.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "market_history.csv")

YEARS = 10
SLEEP_SECONDS = 0.3

INPUT_TICKER_RAW = os.getenv("INPUT_TICKER", "").strip()
INPUT_NAME = os.getenv("INPUT_NAME", "").strip()


def clean_num(value):
    if value is None:
        return None

    text = str(value).replace(",", "").replace(" ", "").strip()

    if text in {"", "-", "None", "nan"}:
        return None

    try:
        return float(text)
    except Exception:
        return None


def clean_ticker(value):
    if value is None:
        return None

    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())

    return digits.zfill(6) if digits else None


def load_market_data():
    if not os.path.exists(MARKET_FILE):
        raise RuntimeError("data/market_data.csv가 없습니다.")

    market = pd.read_csv(MARKET_FILE, dtype=str)

    rename_map = {}

    if "ticker" in market.columns and "종목코드" not in market.columns:
        rename_map["ticker"] = "종목코드"

    if "name" in market.columns and "종목명" not in market.columns:
        rename_map["name"] = "종목명"

    if "price" in market.columns and "현재가" not in market.columns:
        rename_map["price"] = "현재가"

    if "market_cap_eok" in market.columns and "현재시총_억원" not in market.columns:
        rename_map["market_cap_eok"] = "현재시총_억원"

    if rename_map:
        market = market.rename(columns=rename_map)

    required = {"종목코드", "종목명"}

    if not required.issubset(market.columns):
        raise RuntimeError("market_data.csv에 종목코드/종목명 열이 없습니다.")

    market["종목코드"] = market["종목코드"].map(clean_ticker)

    for column in ["현재가", "현재시총_억원", "상장주식수", "시가총액"]:
        if column in market.columns:
            market[column] = pd.to_numeric(market[column], errors="coerce")

    if "현재시총_억원" not in market.columns and "시가총액" in market.columns:
        market["현재시총_억원"] = market["시가총액"] / 100_000_000

    return market.dropna(subset=["종목코드"]).drop_duplicates("종목코드")


def read_target_file(path):
    if not os.path.exists(path):
        return pd.DataFrame(columns=["ticker", "name"])

    try:
        data = pd.read_csv(path, dtype=str)
    except Exception:
        return pd.DataFrame(columns=["ticker", "name"])

    if {"ticker", "name"}.issubset(data.columns):
        result = data[["ticker", "name"]].copy()
    elif {"종목코드", "종목명"}.issubset(data.columns):
        result = data[["종목코드", "종목명"]].copy()
        result.columns = ["ticker", "name"]
    else:
        return pd.DataFrame(columns=["ticker", "name"])

    result["ticker"] = result["ticker"].map(clean_ticker)
    result["name"] = result["name"].astype(str).str.strip()

    return result.dropna(subset=["ticker"])


def load_targets(market):
    if INPUT_TICKER_RAW:
        ticker = clean_ticker(INPUT_TICKER_RAW)
        if not ticker:
            raise RuntimeError("입력된 종목코드가 올바르지 않습니다.")

        market_row = market[market["종목코드"] == ticker]
        resolved_name = INPUT_NAME
        if not resolved_name and not market_row.empty:
            resolved_name = str(market_row.iloc[0]["종목명"])

        return pd.DataFrame([{
            "ticker": ticker,
            "name": resolved_name or ticker,
        }])

    frames = []

    # 앱에서 실제 조회 가능한 종목 목록
    for path in [
        FINANCIAL_FILE,
        FAVORITES_FILE,
        SEARCH_HISTORY_FILE,
    ]:
        target = read_target_file(path)

        if not target.empty:
            frames.append(target)

    if not frames:
        fallback = market[["종목코드", "종목명"]].head(20).copy()
        fallback.columns = ["ticker", "name"]
        frames.append(fallback)

    targets = pd.concat(frames, ignore_index=True)
    targets["ticker"] = targets["ticker"].map(clean_ticker)
    targets["name"] = targets["name"].astype(str).str.strip()

    targets = (
        targets.dropna(subset=["ticker"])
        .drop_duplicates("ticker", keep="last")
        .reset_index(drop=True)
    )

    return targets


def infer_shares(row):
    listed_shares = clean_num(row.get("상장주식수"))

    if listed_shares and listed_shares > 0:
        return listed_shares

    current_price = clean_num(row.get("현재가"))
    current_market_cap_eok = clean_num(row.get("현재시총_억원"))

    if (
        current_price
        and current_price > 0
        and current_market_cap_eok
        and current_market_cap_eok > 0
    ):
        return current_market_cap_eok * 100_000_000 / current_price

    return None


def read_existing_history():
    if not os.path.exists(OUTPUT_FILE):
        return pd.DataFrame()

    try:
        existing = pd.read_csv(
            OUTPUT_FILE,
            dtype={"ticker": str},
            parse_dates=["date"],
        )
    except Exception:
        return pd.DataFrame()

    if existing.empty:
        return existing

    existing["ticker"] = existing["ticker"].map(clean_ticker)

    return existing


def fetch_daily_price(ticker, start_date, end_date):
    attempts = 3

    for attempt in range(1, attempts + 1):
        try:
            data = fdr.DataReader(
                ticker,
                start_date.strftime("%Y-%m-%d"),
                end_date.strftime("%Y-%m-%d"),
            )

            if data is None or data.empty:
                return pd.DataFrame()

            return data

        except Exception as exc:
            print(
                f"  - 주가 수집 재시도 {attempt}/{attempts}: {exc}",
                flush=True,
            )

            if attempt < attempts:
                time.sleep(attempt * 2)

    return pd.DataFrame()


def normalize_price_data(price_data, name, ticker, shares):
    if price_data is None or price_data.empty:
        return pd.DataFrame()

    price_data = price_data.reset_index()

    date_column = price_data.columns[0]

    if "Close" in price_data.columns:
        close_column = "Close"
    elif "종가" in price_data.columns:
        close_column = "종가"
    else:
        return pd.DataFrame()

    result = pd.DataFrame(
        {
            "name": name,
            "ticker": ticker,
            "date": pd.to_datetime(
                price_data[date_column],
                errors="coerce",
            ),
            "price": pd.to_numeric(
                price_data[close_column],
                errors="coerce",
            ),
        }
    )

    result = result.dropna(subset=["date", "price"])
    result = result[result["price"] > 0]

    if result.empty:
        return result

    result["shares"] = float(shares)
    result["market_cap"] = result["price"] * result["shares"]

    return result[
        [
            "name",
            "ticker",
            "date",
            "price",
            "shares",
            "market_cap",
        ]
    ]


def main():
    print("=== 일별 시가총액 업데이트 시작 ===", flush=True)

    os.makedirs(DATA_DIR, exist_ok=True)

    market = load_market_data()
    targets = load_targets(market)
    existing = read_existing_history()

    print(f"수집 대상: {len(targets):,}개 종목", flush=True)

    today = datetime.today()
    default_start = today - timedelta(days=365 * YEARS + 60)

    collected = []

    for index, target in targets.iterrows():
        ticker = target["ticker"]
        name = target["name"]

        print(
            f"[{index + 1}/{len(targets)}] {name} ({ticker})",
            flush=True,
        )

        market_row = market[market["종목코드"] == ticker]

        if market_row.empty:
            print("  - market_data.csv에 종목 없음", flush=True)
            continue

        shares = infer_shares(market_row.iloc[0])

        if not shares or shares <= 0:
            print("  - 상장주식수 계산 불가", flush=True)
            continue

        start_date = default_start

        if not existing.empty:
            old = existing[existing["ticker"] == ticker]

            if not old.empty:
                last_date = pd.to_datetime(old["date"], errors="coerce").max()

                if pd.notna(last_date):
                    start_date = max(
                        default_start,
                        last_date.to_pydatetime() - timedelta(days=7),
                    )

        price_data = fetch_daily_price(
            ticker,
            start_date,
            today,
        )

        normalized = normalize_price_data(
            price_data,
            name,
            ticker,
            shares,
        )

        if normalized.empty:
            print("  - 일별 주가 데이터 없음", flush=True)
            continue

        collected.append(normalized)

        print(
            f"  - {len(normalized):,}일 수집",
            flush=True,
        )

        time.sleep(SLEEP_SECONDS)

    if not collected and existing.empty:
        raise RuntimeError("일별 주가 데이터를 한 건도 수집하지 못했습니다.")

    frames = []

    if not existing.empty:
        frames.append(existing)

    if collected:
        frames.extend(collected)

    result = pd.concat(frames, ignore_index=True)

    result["ticker"] = result["ticker"].map(clean_ticker)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["price"] = pd.to_numeric(result["price"], errors="coerce")
    result["shares"] = pd.to_numeric(result["shares"], errors="coerce")
    result["market_cap"] = pd.to_numeric(
        result["market_cap"],
        errors="coerce",
    )

    result = (
        result.dropna(
            subset=[
                "ticker",
                "date",
                "price",
                "market_cap",
            ]
        )
        .drop_duplicates(
            subset=["ticker", "date"],
            keep="last",
        )
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )

    result.to_csv(
        OUTPUT_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"market_history.csv 완료: {len(result):,}행",
        flush=True,
    )
    print("=== 일별 시가총액 업데이트 완료 ===", flush=True)


if __name__ == "__main__":
    main()
