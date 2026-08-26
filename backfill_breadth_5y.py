import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import requests
import FinanceDataReader as fdr

DATA_DIR = "data"
CLOSE_FILE = os.path.join(DATA_DIR, "breadth_close_history.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "breadth_history.csv")
MARKETS = ["KOSPI", "KOSDAQ"]
BACKFILL_YEARS = int(os.getenv("BREADTH_BACKFILL_YEARS", "5"))
REQUEST_COUNT = int(os.getenv("BREADTH_BACKFILL_COUNT", "1650"))
MAX_WORKERS = int(os.getenv("BREADTH_WORKERS", "12"))
NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"
os.makedirs(DATA_DIR, exist_ok=True)

def clean_ticker(value):
    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""

def get_market_tickers(market):
    listing = fdr.StockListing(market)
    code_col = "Code" if "Code" in listing.columns else "Symbol"
    tickers = sorted({clean_ticker(x) for x in listing[code_col].dropna() if clean_ticker(x)})
    if not tickers:
        raise RuntimeError(f"{market} 종목 목록을 가져오지 못했습니다.")
    return tickers

def fetch_history(ticker, market):
    params = {"symbol": ticker, "timeframe": "day", "count": str(REQUEST_COUNT), "requestType": "0"}
    headers = {"User-Agent": "Mozilla/5.0", "Referer": f"https://finance.naver.com/item/main.naver?code={ticker}"}
    last_error = None
    for attempt in range(4):
        try:
            r = requests.get(NAVER_CHART_URL, params=params, headers=headers, timeout=(10, 35))
            r.raise_for_status()
            try:
                xml_text = r.content.decode("euc-kr")
            except UnicodeDecodeError:
                xml_text = r.content.decode("cp949", errors="replace")
            xml_text = xml_text.lstrip("\ufeff\r\n\t ")
            if xml_text.startswith("<?xml"):
                p = xml_text.find("?>")
                if p >= 0:
                    xml_text = xml_text[p + 2:]
            root = ET.fromstring(xml_text)
            rows = []
            for item in root.findall(".//item"):
                parts = item.attrib.get("data", "").split("|")
                if len(parts) < 6:
                    continue
                rows.append({
                    "date": pd.to_datetime(parts[0], format="%Y%m%d", errors="coerce"),
                    "market": market,
                    "ticker": ticker,
                    "close": pd.to_numeric(parts[4], errors="coerce"),
                })
            df = pd.DataFrame(rows)
            if df.empty:
                return df
            df = df.dropna(subset=["date", "close"]).sort_values("date")
            df = df[df["close"] > 0]
            return df
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))
    print(f"{market} {ticker} 실패: {last_error}", flush=True)
    return pd.DataFrame()

def collect():
    jobs = []
    for market in MARKETS:
        tickers = get_market_tickers(market)
        print(f"{market}: {len(tickers):,}개", flush=True)
        jobs += [(t, market) for t in tickers]
    frames = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = {ex.submit(fetch_history, t, m): (t, m) for t, m in jobs}
        total = len(futures)
        for i, future in enumerate(as_completed(futures), 1):
            try:
                df = future.result()
                if not df.empty:
                    frames.append(df)
            except Exception as exc:
                print(exc, flush=True)
            if i % 100 == 0 or i == total:
                print(f"수집 {i:,}/{total:,}", flush=True)
    if not frames:
        raise RuntimeError("과거 종가 수집 실패")
    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(["date", "market", "ticker"], keep="last").sort_values(["market", "ticker", "date"])
    df["change"] = df.groupby(["market", "ticker"])["close"].pct_change() * 100
    return df

def calculate(close_df):
    rows = []
    display_start = pd.Timestamp.today().normalize() - pd.DateOffset(years=BACKFILL_YEARS)
    for market in MARKETS:
        m = close_df[close_df["market"] == market].copy().sort_values(["ticker", "date"])
        if m.empty:
            continue
        for w in [20, 60, 120, 200]:
            m[f"ma{w}"] = m.groupby("ticker")["close"].transform(lambda s, w=w: s.rolling(w, min_periods=w).mean())
        m["high_252"] = m.groupby("ticker")["close"].transform(lambda s: s.rolling(252, min_periods=120).max())
        m["low_252"] = m.groupby("ticker")["close"].transform(lambda s: s.rolling(252, min_periods=120).min())
        ad_line = 0
        for date, day in m.groupby("date", sort=True):
            adv = int((day["change"] > 0).sum())
            dec = int((day["change"] < 0).sum())
            unc = int((day["change"].fillna(0) == 0).sum())
            ad_net = adv - dec
            ad_line += ad_net
            if date < display_start:
                continue
            row = {
                "date": date, "market": market, "advancers": adv, "decliners": dec,
                "unchanged": unc, "ad_net": ad_net, "ad_line": ad_line,
                "new_high_52w": int((day["close"] >= day["high_252"]).fillna(False).sum()),
                "new_low_52w": int((day["close"] <= day["low_252"]).fillna(False).sum()),
            }
            for w in [20, 60, 120, 200]:
                ma = day[f"ma{w}"]
                valid = ma.notna()
                row[f"above_ma{w}"] = float((day.loc[valid, "close"] > ma.loc[valid]).mean() * 100) if valid.any() else None
            rows.append(row)
    if not rows:
        raise RuntimeError("Breadth 계산 결과 없음")
    return pd.DataFrame(rows).sort_values(["market", "date"])

def save(close_df, breadth_df):
    close_df.to_csv(CLOSE_FILE, index=False, encoding="utf-8-sig")
    breadth_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    for market in MARKETS:
        x = breadth_df[breadth_df["market"] == market]
        if not x.empty:
            print(f"{market}: {x['date'].min().date()} ~ {x['date'].max().date()} / {len(x):,}일", flush=True)

def main():
    print(f"5년 백필 시작 / count={REQUEST_COUNT} / workers={MAX_WORKERS}", flush=True)
    close_df = collect()
    breadth_df = calculate(close_df)
    save(close_df, breadth_df)
    print("5년 Market Breadth 백필 완료", flush=True)

if __name__ == "__main__":
    main()
