import os
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta

import pandas as pd
import requests

try:
    from pykrx import stock
except Exception:
    stock = None

try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None

DATA_DIR = "data"
CLOSE_FILE = os.path.join(DATA_DIR, "breadth_close_history.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "breadth_history.csv")
LOOKBACK_CALENDAR_DAYS = 430
MARKETS = ["KOSPI", "KOSDAQ"]
MAX_WORKERS = 10
NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"

os.makedirs(DATA_DIR, exist_ok=True)


def clean_ticker(value) -> str:
    return str(value).strip().replace(".0", "").zfill(6)


def load_close_history() -> pd.DataFrame:
    cols = ["date", "market", "ticker", "close", "change"]
    if not os.path.exists(CLOSE_FILE):
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(CLOSE_FILE, dtype={"ticker": str})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].map(clean_ticker)
    for col in ["close", "change"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "market", "ticker", "close"])[cols]


def get_market_tickers(market: str) -> list[str]:
    today = datetime.today().strftime("%Y%m%d")

    if stock is not None:
        try:
            tickers = stock.get_market_ticker_list(today, market=market)
            if tickers:
                return sorted({clean_ticker(x) for x in tickers})
        except Exception as exc:
            print(f"pykrx {market} 종목목록 실패: {exc}", flush=True)

    if fdr is not None:
        try:
            listing = fdr.StockListing(market)
            code_col = "Code" if "Code" in listing.columns else "Symbol"
            tickers = listing[code_col].dropna().map(clean_ticker).tolist()
            if tickers:
                return sorted(set(tickers))
        except Exception as exc:
            print(f"FinanceDataReader {market} 종목목록 실패: {exc}", flush=True)

    raise RuntimeError(f"{market} 종목 목록을 가져오지 못했습니다.")


def fetch_naver_history(ticker: str, market: str, count: int) -> pd.DataFrame:
    params = {
        "symbol": ticker,
        "timeframe": "day",
        "count": str(count),
        "requestType": "0",
    }
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": f"https://finance.naver.com/item/main.naver?code={ticker}",
    }

    last_error = None
    for attempt in range(4):
        try:
            response = requests.get(
                NAVER_CHART_URL,
                params=params,
                headers=headers,
                timeout=(10, 30),
            )
            response.raise_for_status()

            # Naver chart XML declares EUC-KR. Python's built-in Expat parser
            # can raise "multi-byte encodings are not supported" when the
            # raw bytes are passed directly. Decode first, remove the XML
            # declaration, and then parse the Unicode text.
            raw_bytes = response.content
            try:
                xml_text = raw_bytes.decode("euc-kr")
            except UnicodeDecodeError:
                try:
                    xml_text = raw_bytes.decode("cp949")
                except UnicodeDecodeError:
                    xml_text = raw_bytes.decode("utf-8", errors="replace")

            xml_text = xml_text.lstrip("\ufeff\r\n\t ")
            if xml_text.startswith("<?xml"):
                declaration_end = xml_text.find("?>")
                if declaration_end >= 0:
                    xml_text = xml_text[declaration_end + 2:]

            root = ET.fromstring(xml_text)
            rows = []
            for item in root.findall(".//item"):
                raw = item.attrib.get("data", "")
                parts = raw.split("|")
                if len(parts) < 6:
                    continue
                date_text, _open, _high, _low, close, _volume = parts[:6]
                rows.append({
                    "date": pd.to_datetime(date_text, format="%Y%m%d", errors="coerce"),
                    "market": market,
                    "ticker": ticker,
                    "close": pd.to_numeric(close, errors="coerce"),
                })

            frame = pd.DataFrame(rows)
            if frame.empty:
                return pd.DataFrame(columns=["date", "market", "ticker", "close", "change"])

            frame = frame.dropna(subset=["date", "close"]).sort_values("date")
            frame = frame[frame["close"] > 0]
            frame["change"] = frame["close"].pct_change() * 100
            return frame[["date", "market", "ticker", "close", "change"]]
        except Exception as exc:
            last_error = exc
            time.sleep(1.5 * (attempt + 1))

    print(f"{market} {ticker} 수집 실패: {last_error}", flush=True)
    return pd.DataFrame(columns=["date", "market", "ticker", "close", "change"])


def update_close_history() -> pd.DataFrame:
    old = load_close_history()
    end = pd.Timestamp.today().normalize()
    cutoff = end - pd.Timedelta(days=LOOKBACK_CALENDAR_DAYS)

    if old.empty:
        request_count = 330
    else:
        missing_calendar_days = max(7, (end - old["date"].max()).days + 7)
        request_count = min(330, max(15, int(missing_calendar_days * 0.75) + 10))

    jobs = []
    for market in MARKETS:
        tickers = get_market_tickers(market)
        print(f"{market}: {len(tickers):,}개 종목", flush=True)
        jobs.extend((ticker, market) for ticker in tickers)

    new_frames = []
    total = len(jobs)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_naver_history, ticker, market, request_count): (ticker, market)
            for ticker, market in jobs
        }
        for idx, future in enumerate(as_completed(futures), start=1):
            ticker, market = futures[future]
            try:
                frame = future.result()
                if not frame.empty:
                    new_frames.append(frame)
            except Exception as exc:
                print(f"[{idx}/{total}] {market} {ticker} 실패: {exc}", flush=True)
            if idx % 100 == 0 or idx == total:
                print(f"수집 진행: {idx:,}/{total:,}", flush=True)

    if not new_frames and old.empty:
        raise RuntimeError("네이버에서도 종가 데이터를 수집하지 못했습니다.")

    all_df = pd.concat([old] + new_frames, ignore_index=True)
    all_df = all_df[all_df["date"] >= cutoff]
    all_df = (
        all_df.drop_duplicates(["date", "market", "ticker"], keep="last")
        .sort_values(["market", "ticker", "date"])
        .reset_index(drop=True)
    )
    all_df["change"] = all_df.groupby(["market", "ticker"])["close"].pct_change() * 100
    all_df.to_csv(CLOSE_FILE, index=False, encoding="utf-8-sig")
    return all_df


def calculate_breadth(close_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for market in MARKETS:
        m = close_df[close_df["market"] == market].copy()
        if m.empty:
            continue

        m = m.sort_values(["ticker", "date"])
        for window in [20, 60, 120, 200]:
            m[f"ma{window}"] = m.groupby("ticker")["close"].transform(
                lambda s, w=window: s.rolling(w, min_periods=w).mean()
            )

        m["high_252"] = m.groupby("ticker")["close"].transform(
            lambda s: s.rolling(252, min_periods=120).max()
        )
        m["low_252"] = m.groupby("ticker")["close"].transform(
            lambda s: s.rolling(252, min_periods=120).min()
        )

        ad_line = 0
        for date, day in m.groupby("date", sort=True):
            advancers = int((day["change"] > 0).sum())
            decliners = int((day["change"] < 0).sum())
            unchanged = int((day["change"].fillna(0) == 0).sum())
            ad_net = advancers - decliners
            ad_line += ad_net

            row = {
                "date": date,
                "market": market,
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
                "ad_net": ad_net,
                "ad_line": ad_line,
                "new_high_52w": int((day["close"] >= day["high_252"]).fillna(False).sum()),
                "new_low_52w": int((day["close"] <= day["low_252"]).fillna(False).sum()),
            }
            for window in [20, 60, 120, 200]:
                ma = day[f"ma{window}"]
                valid = ma.notna()
                row[f"above_ma{window}"] = (
                    float((day.loc[valid, "close"] > ma.loc[valid]).mean() * 100)
                    if valid.any() else None
                )
            rows.append(row)

    if not rows:
        raise RuntimeError("Breadth 계산 결과가 없습니다.")

    out = pd.DataFrame(rows).sort_values(["market", "date"])
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    return out


def main():
    close_df = update_close_history()
    result = calculate_breadth(close_df)
    print(f"완료: {OUTPUT_FILE} ({len(result):,}행)", flush=True)


if __name__ == "__main__":
    main()
