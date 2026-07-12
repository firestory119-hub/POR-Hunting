import os
import time
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr


DATA_DIR = "data"
HISTORY_FILE = os.path.join(DATA_DIR, "market_history.csv")
LOG_FILE = os.path.join(DATA_DIR, "daily_update_log.csv")

LOOKBACK_DAYS = 7
SLEEP_SECONDS = 0.25


def clean_ticker(value):
    if value is None:
        return None
    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else None


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


def load_history():
    if not os.path.exists(HISTORY_FILE):
        raise RuntimeError("data/market_history.csv가 없습니다.")

    history = pd.read_csv(
        HISTORY_FILE,
        dtype={"ticker": str},
        parse_dates=["date"],
    )

    required = {"name", "ticker", "date", "price", "shares", "market_cap"}
    if not required.issubset(history.columns):
        raise RuntimeError(
            "market_history.csv 필수 열: "
            "name,ticker,date,price,shares,market_cap"
        )

    history["ticker"] = history["ticker"].map(clean_ticker)
    history["date"] = pd.to_datetime(history["date"], errors="coerce")

    for column in ["price", "shares", "market_cap"]:
        history[column] = pd.to_numeric(history[column], errors="coerce")

    return (
        history.dropna(
            subset=["ticker", "date", "price", "shares", "market_cap"]
        )
        .drop_duplicates(["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )


def fetch_daily(ticker, start_date, end_date):
    for attempt in range(1, 4):
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
            print(f"  재시도 {attempt}/3: {exc}", flush=True)
            if attempt < 3:
                time.sleep(attempt * 2)
    return pd.DataFrame()


def normalize(price_data, name, ticker, shares):
    if price_data is None or price_data.empty:
        return pd.DataFrame()

    price_data = price_data.reset_index()
    date_col = price_data.columns[0]
    close_col = "Close" if "Close" in price_data.columns else (
        "종가" if "종가" in price_data.columns else None
    )
    if close_col is None:
        return pd.DataFrame()

    out = pd.DataFrame({
        "name": name,
        "ticker": ticker,
        "date": pd.to_datetime(price_data[date_col], errors="coerce"),
        "price": pd.to_numeric(price_data[close_col], errors="coerce"),
    }).dropna(subset=["date", "price"])

    out = out[out["price"] > 0]
    if out.empty:
        return out

    out["shares"] = float(shares)
    out["market_cap"] = out["price"] * out["shares"]

    return out[["name", "ticker", "date", "price", "shares", "market_cap"]]


def append_log(started_at, target_count, success_count, failure_count, added_rows):
    finished_at = datetime.now()
    row = pd.DataFrame([{
        "started_at": started_at.strftime("%Y-%m-%d %H:%M:%S"),
        "finished_at": finished_at.strftime("%Y-%m-%d %H:%M:%S"),
        "target_count": target_count,
        "success_count": success_count,
        "failure_count": failure_count,
        "added_rows": added_rows,
        "elapsed_seconds": round((finished_at - started_at).total_seconds(), 1),
    }])

    if os.path.exists(LOG_FILE):
        try:
            old = pd.read_csv(LOG_FILE)
            row = pd.concat([old, row], ignore_index=True)
        except Exception:
            pass

    row.tail(365).to_csv(LOG_FILE, index=False, encoding="utf-8-sig")


def main():
    started_at = datetime.now()
    today = datetime.today()
    history = load_history()

    targets = (
        history[["name", "ticker", "shares"]]
        .drop_duplicates("ticker", keep="last")
        .reset_index(drop=True)
    )

    print(f"자동 최신화 대상: {len(targets):,}개 종목", flush=True)

    collected = []
    success_count = 0
    failure_count = 0

    for index, target in targets.iterrows():
        ticker = clean_ticker(target["ticker"])
        name = str(target["name"])
        shares = clean_num(target["shares"])
        old = history[history["ticker"] == ticker]
        last_date = old["date"].max()

        if not shares or shares <= 0 or pd.isna(last_date):
            failure_count += 1
            continue

        start_date = last_date.to_pydatetime() - timedelta(days=LOOKBACK_DAYS)

        print(
            f"[{index + 1}/{len(targets)}] {name} ({ticker}) "
            f"{start_date:%Y-%m-%d}~{today:%Y-%m-%d}",
            flush=True,
        )

        price_data = fetch_daily(ticker, start_date, today)
        rows = normalize(price_data, name, ticker, shares)

        if rows.empty:
            failure_count += 1
        else:
            collected.append(rows)
            success_count += 1

        time.sleep(SLEEP_SECONDS)

    before_count = len(history)
    frames = [history] + collected
    result = pd.concat(frames, ignore_index=True)

    result["ticker"] = result["ticker"].map(clean_ticker)
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    for column in ["price", "shares", "market_cap"]:
        result[column] = pd.to_numeric(result[column], errors="coerce")

    result = (
        result.dropna(
            subset=["ticker", "date", "price", "shares", "market_cap"]
        )
        .drop_duplicates(["ticker", "date"], keep="last")
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )

    result.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    added_rows = max(0, len(result) - before_count)

    append_log(
        started_at,
        len(targets),
        success_count,
        failure_count,
        added_rows,
    )

    print(
        f"완료: 성공 {success_count}, 실패·데이터없음 {failure_count}, "
        f"순증가 {added_rows}행",
        flush=True,
    )


if __name__ == "__main__":
    main()
