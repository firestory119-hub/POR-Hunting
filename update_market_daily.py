
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr

DATA_DIR = "data"
MARKET_FILE = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_FILE = os.path.join(DATA_DIR, "financial_data.csv")
HISTORY_FILE = os.path.join(DATA_DIR, "market_history.csv")
YEARS = 10


def clean_num(value):
    if value is None:
        return None
    s = str(value).replace(",", "").replace(" ", "").strip()
    if s in {"", "-", "nan", "None"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def clean_ticker(value):
    if value is None:
        return None
    digits = "".join(ch for ch in str(value).replace(".0", "") if ch.isdigit())
    return digits.zfill(6) if digits else None


def load_market():
    df = pd.read_csv(MARKET_FILE, dtype=str)
    rename = {}
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename["ticker"] = "종목코드"
    if "name" in df.columns and "종목명" not in df.columns:
        rename["name"] = "종목명"
    if "price" in df.columns and "현재가" not in df.columns:
        rename["price"] = "현재가"
    if "market_cap_eok" in df.columns and "현재시총_억원" not in df.columns:
        rename["market_cap_eok"] = "현재시총_억원"
    if rename:
        df = df.rename(columns=rename)

    df["종목코드"] = df["종목코드"].map(clean_ticker)
    for col in ["현재가", "현재시총_억원", "상장주식수"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def load_targets(market):
    targets = []
    if os.path.exists(FINANCIAL_FILE):
        fin = pd.read_csv(FINANCIAL_FILE, dtype=str)
        if {"ticker", "name"}.issubset(fin.columns):
            targets.append(fin[["ticker", "name"]])

    for filename in ["favorites.csv", "search_history.csv"]:
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            df = pd.read_csv(path, dtype=str)
            if {"ticker", "name"}.issubset(df.columns):
                targets.append(df[["ticker", "name"]])

    if not targets:
        fallback = market[["종목코드", "종목명"]].head(20).copy()
        fallback.columns = ["ticker", "name"]
        targets.append(fallback)

    out = pd.concat(targets, ignore_index=True)
    out["ticker"] = out["ticker"].map(clean_ticker)
    return out.dropna(subset=["ticker"]).drop_duplicates("ticker").reset_index(drop=True)


def infer_shares(row):
    shares = clean_num(row.get("상장주식수"))
    if shares and shares > 0:
        return shares

    price = clean_num(row.get("현재가"))
    mcap_eok = clean_num(row.get("현재시총_억원"))
    if price and mcap_eok:
        return mcap_eok * 100_000_000 / price
    return None


def main():
    print("=== 일별 시가총액 업데이트 시작 ===", flush=True)
    market = load_market()
    targets = load_targets(market)
    print(f"수집 대상: {len(targets):,}개", flush=True)

    end = datetime.today()
    start = end - timedelta(days=365 * YEARS + 30)
    rows = []

    for idx, target in targets.iterrows():
        ticker = target["ticker"]
        name = target["name"]
        print(f"[{idx+1}/{len(targets)}] {name} ({ticker})", flush=True)

        mrow = market[market["종목코드"] == ticker]
        if mrow.empty:
            continue
        shares = infer_shares(mrow.iloc[0])
        if not shares:
            print("  - 상장주식수 계산 불가", flush=True)
            continue

        try:
            price = fdr.DataReader(ticker, start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d"))
        except Exception as exc:
            print(f"  - 주가 수집 실패: {exc}", flush=True)
            continue

        if price is None or price.empty:
            continue

        price = price.reset_index()
        date_col = price.columns[0]
        close_col = "Close" if "Close" in price.columns else ("종가" if "종가" in price.columns else None)
        if close_col is None:
            continue

        one = pd.DataFrame({
            "name": name,
            "ticker": ticker,
            "date": pd.to_datetime(price[date_col], errors="coerce"),
            "price": pd.to_numeric(price[close_col], errors="coerce"),
        }).dropna(subset=["date", "price"])
        one["shares"] = float(shares)
        one["market_cap"] = one["price"] * one["shares"]
        rows.append(one)
        time.sleep(0.3)

    if not rows:
        raise RuntimeError("일별 주가 데이터를 수집하지 못했습니다.")

    result = pd.concat(rows, ignore_index=True).sort_values(["ticker", "date"])
    result.to_csv(HISTORY_FILE, index=False, encoding="utf-8-sig")
    print(f"market_history.csv 완료: {len(result):,}행", flush=True)


if __name__ == "__main__":
    main()
