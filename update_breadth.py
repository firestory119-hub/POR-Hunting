"""Fast incremental KOSPI/KOSDAQ market-breadth builder (no KRX login).

First run
---------
Downloads roughly 330 daily closes per listed stock from Naver in parallel and
stores a rolling local cache in ``data/breadth_close_history.csv``.

Later runs
----------
Reads the newest all-stock prices already maintained by this repository in
``data/market_data.csv`` and appends only one new trading-day row per stock.
Therefore normal daily runs avoid thousands of HTTP calls and usually finish
quickly.
"""

from __future__ import annotations

import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import pandas as pd
import requests

try:
    import FinanceDataReader as fdr
except Exception:
    fdr = None

DATA_DIR = "data"
MARKET_DATA_FILE = os.path.join(DATA_DIR, "market_data.csv")
CLOSE_FILE = os.path.join(DATA_DIR, "breadth_close_history.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "breadth_history.csv")

MARKETS = ("KOSPI", "KOSDAQ")
WINDOWS = (20, 60, 120, 200)
HISTORY_TRADING_DAYS = 330
KEEP_CALENDAR_DAYS = 560
MAX_WORKERS = int(os.getenv("BREADTH_WORKERS", "28"))
CHECKPOINT_EVERY = 100
NAVER_CHART_URL = "https://fchart.stock.naver.com/sise.nhn"

os.makedirs(DATA_DIR, exist_ok=True)
_thread_local = threading.local()


def clean_ticker(value) -> str:
    return str(value).strip().replace(".0", "").zfill(6)


def get_session() -> requests.Session:
    session = getattr(_thread_local, "session", None)
    if session is None:
        session = requests.Session()
        adapter = requests.adapters.HTTPAdapter(pool_connections=40, pool_maxsize=40)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        _thread_local.session = session
    return session


def decode_xml(raw: bytes) -> str:
    for encoding in ("euc-kr", "cp949", "utf-8"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        text = raw.decode("utf-8", errors="replace")

    text = text.lstrip("\ufeff\r\n\t ")
    text = re.sub(r"^<\?xml[^>]*\?>", "", text, count=1).lstrip()
    return text


def market_ticker_map() -> dict[str, str]:
    """Build KOSPI/KOSDAQ ticker map without KRX login.

    Primary source is FinanceDataReader's exchange listing. If that is
    temporarily unavailable, fall back to cached ticker/market pairs from the
    existing close-history file.
    """
    mapping: dict[str, str] = {}
    errors: list[str] = []

    if fdr is not None:
        for market in MARKETS:
            try:
                listing = fdr.StockListing(market)
                code_col = next(
                    (c for c in ("Code", "Symbol", "종목코드") if c in listing.columns),
                    None,
                )
                if code_col is None:
                    raise RuntimeError(f"종목코드 열을 찾지 못함: {list(listing.columns)}")

                codes = (
                    listing[code_col]
                    .dropna()
                    .astype(str)
                    .str.replace(r"\.0$", "", regex=True)
                    .str.zfill(6)
                )
                for ticker in codes:
                    mapping[clean_ticker(ticker)] = market
                print(f"{market} 종목목록: {len(codes):,}개", flush=True)
            except Exception as exc:
                errors.append(f"{market}: {exc}")

    # A partially completed first run can resume even if the listing endpoint
    # is temporarily unavailable.
    if not mapping and os.path.exists(CLOSE_FILE):
        try:
            cached = pd.read_csv(CLOSE_FILE, dtype={"ticker": str})
            if {"ticker", "market"}.issubset(cached.columns):
                cached = cached.dropna(subset=["ticker", "market"])
                cached = cached[cached["market"].isin(MARKETS)]
                mapping = {
                    clean_ticker(row.ticker): str(row.market)
                    for row in cached[["ticker", "market"]].drop_duplicates().itertuples(index=False)
                }
                if mapping:
                    print(f"캐시 종목목록 사용: {len(mapping):,}개", flush=True)
        except Exception as exc:
            errors.append(f"cache: {exc}")

    if not mapping:
        raise RuntimeError(
            "KOSPI/KOSDAQ 종목 목록을 가져오지 못했습니다. "
            + " / ".join(errors)
        )
    return mapping


def load_close_history() -> pd.DataFrame:
    columns = ["date", "market", "ticker", "close", "change"]
    if not os.path.exists(CLOSE_FILE):
        return pd.DataFrame(columns=columns)

    df = pd.read_csv(CLOSE_FILE, dtype={"ticker": str})
    for col in columns:
        if col not in df.columns:
            df[col] = pd.NA
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].map(clean_ticker)
    df["close"] = pd.to_numeric(df["close"], errors="coerce")
    df["change"] = pd.to_numeric(df["change"], errors="coerce")
    return (
        df.dropna(subset=["date", "market", "ticker", "close"])[columns]
        .drop_duplicates(["date", "market", "ticker"], keep="last")
    )


def save_close_history(df: pd.DataFrame) -> None:
    df = df.sort_values(["market", "ticker", "date"]).copy()
    df["change"] = df.groupby(["market", "ticker"], sort=False)["close"].pct_change() * 100
    df.to_csv(CLOSE_FILE, index=False, encoding="utf-8-sig")


def fetch_naver_history(ticker: str, market: str) -> pd.DataFrame:
    params = {
        "symbol": ticker,
        "timeframe": "day",
        "count": str(HISTORY_TRADING_DAYS),
        "requestType": "0",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/150 Safari/537.36",
        "Referer": f"https://finance.naver.com/item/main.naver?code={ticker}",
        "Accept": "text/xml,application/xml,text/html;q=0.9,*/*;q=0.8",
    }

    last_error: Exception | None = None
    for attempt in range(3):
        try:
            response = get_session().get(
                NAVER_CHART_URL,
                params=params,
                headers=headers,
                timeout=(5, 15),
            )
            response.raise_for_status()
            root = ET.fromstring(decode_xml(response.content))
            rows = []
            for item in root.findall(".//item"):
                parts = item.attrib.get("data", "").split("|")
                if len(parts) < 6:
                    continue
                date_text, _open, _high, _low, close, _volume = parts[:6]
                rows.append((date_text, close))

            if not rows:
                raise ValueError("빈 차트 응답")

            frame = pd.DataFrame(rows, columns=["date", "close"])
            frame["date"] = pd.to_datetime(frame["date"], format="%Y%m%d", errors="coerce")
            frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
            frame = frame.dropna(subset=["date", "close"])
            frame = frame[frame["close"] > 0].sort_values("date")
            frame["market"] = market
            frame["ticker"] = ticker
            frame["change"] = frame["close"].pct_change() * 100
            return frame[["date", "market", "ticker", "close", "change"]]
        except Exception as exc:
            last_error = exc
            time.sleep(0.5 * (attempt + 1))

    print(f"{market} {ticker} 초기수집 실패: {last_error}", flush=True)
    return pd.DataFrame(columns=["date", "market", "ticker", "close", "change"])


def bootstrap_history(ticker_map: dict[str, str], old: pd.DataFrame) -> pd.DataFrame:
    existing_counts = old.groupby("ticker")["date"].nunique().to_dict() if not old.empty else {}
    jobs = [
        (ticker, market)
        for ticker, market in ticker_map.items()
        if existing_counts.get(ticker, 0) < 200
    ]

    if not jobs:
        return old

    print(f"최초/보충 수집 대상: {len(jobs):,}개 (동시 작업 {MAX_WORKERS}개)", flush=True)
    frames = [old] if not old.empty else []
    completed = 0

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(fetch_naver_history, ticker, market): (ticker, market)
            for ticker, market in jobs
        }
        for future in as_completed(futures):
            completed += 1
            try:
                frame = future.result()
                if not frame.empty:
                    frames.append(frame)
            except Exception as exc:
                ticker, market = futures[future]
                print(f"{market} {ticker} 예외: {exc}", flush=True)

            if completed % CHECKPOINT_EVERY == 0 or completed == len(jobs):
                merged = pd.concat(frames, ignore_index=True) if frames else old
                merged = merged.drop_duplicates(["date", "market", "ticker"], keep="last")
                cutoff = pd.Timestamp.today().normalize() - pd.Timedelta(days=KEEP_CALENDAR_DAYS)
                merged = merged[merged["date"] >= cutoff]
                save_close_history(merged)
                frames = [merged]
                print(f"초기수집 진행: {completed:,}/{len(jobs):,} (중간저장 완료)", flush=True)

    return frames[0] if len(frames) == 1 else pd.concat(frames, ignore_index=True)


def append_latest_market_data(close_df: pd.DataFrame, ticker_map: dict[str, str]) -> pd.DataFrame:
    if not os.path.exists(MARKET_DATA_FILE):
        print("data/market_data.csv가 없어 당일 증분 추가를 건너뜁니다.", flush=True)
        return close_df

    market = pd.read_csv(MARKET_DATA_FILE, dtype={"종목코드": str})
    required = {"종목코드", "현재가"}
    if not required.issubset(market.columns):
        print("market_data.csv에 종목코드/현재가 열이 없어 증분 추가를 건너뜁니다.", flush=True)
        return close_df

    market["ticker"] = market["종목코드"].map(clean_ticker)
    market["close"] = pd.to_numeric(market["현재가"], errors="coerce")
    market["market"] = market["ticker"].map(ticker_map)

    if "기준일" in market.columns:
        market["date"] = pd.to_datetime(market["기준일"], errors="coerce")
    else:
        market["date"] = pd.Timestamp.today().normalize()

    latest = market.dropna(subset=["date", "market", "ticker", "close"])
    latest = latest[latest["close"] > 0][["date", "market", "ticker", "close"]]
    latest = latest.drop_duplicates(["date", "market", "ticker"], keep="last")
    latest["change"] = pd.NA

    if latest.empty:
        print("market_data.csv에서 유효한 최신 가격을 찾지 못했습니다.", flush=True)
        return close_df

    latest_date = latest["date"].max()
    cached_date = close_df["date"].max() if not close_df.empty else pd.NaT
    if pd.notna(cached_date) and latest_date <= cached_date:
        print(f"신규 거래일 없음: 캐시 {cached_date:%Y-%m-%d}, market_data {latest_date:%Y-%m-%d}", flush=True)
        return close_df

    out = pd.concat([close_df, latest], ignore_index=True)
    out = out.drop_duplicates(["date", "market", "ticker"], keep="last")
    cutoff = latest_date - pd.Timedelta(days=KEEP_CALENDAR_DAYS)
    out = out[out["date"] >= cutoff]
    print(f"신규 거래일 {latest_date:%Y-%m-%d}: {len(latest):,}개 종목 추가", flush=True)
    return out


def calculate_breadth(close_df: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []

    for market_name in MARKETS:
        m = close_df[close_df["market"] == market_name].copy()
        if m.empty:
            continue

        m = m.sort_values(["ticker", "date"])
        for window in WINDOWS:
            m[f"ma{window}"] = m.groupby("ticker", sort=False)["close"].transform(
                lambda series, w=window: series.rolling(w, min_periods=w).mean()
            )

        m["high_252"] = m.groupby("ticker", sort=False)["close"].transform(
            lambda series: series.rolling(252, min_periods=120).max()
        )
        m["low_252"] = m.groupby("ticker", sort=False)["close"].transform(
            lambda series: series.rolling(252, min_periods=120).min()
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
                "market": market_name,
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
                "ad_net": ad_net,
                "ad_line": ad_line,
                "new_high_52w": int((day["close"] >= day["high_252"]).fillna(False).sum()),
                "new_low_52w": int((day["close"] <= day["low_252"]).fillna(False).sum()),
            }

            for window in WINDOWS:
                ma = day[f"ma{window}"]
                valid = ma.notna()
                row[f"above_ma{window}"] = (
                    round(float((day.loc[valid, "close"] > ma.loc[valid]).mean() * 100), 4)
                    if valid.any()
                    else None
                )
            rows.append(row)

    if not rows:
        raise RuntimeError("Breadth 계산 결과가 없습니다.")

    out = pd.DataFrame(rows).sort_values(["market", "date"]).reset_index(drop=True)
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    return out


def main() -> None:
    started = time.time()
    ticker_map = market_ticker_map()
    close_df = load_close_history()

    # Only stocks without a usable 200-day cache are downloaded from Naver.
    close_df = bootstrap_history(ticker_map, close_df)

    # On normal daily runs, append all current prices from one local CSV read.
    close_df = append_latest_market_data(close_df, ticker_map)
    close_df = close_df.drop_duplicates(["date", "market", "ticker"], keep="last")
    save_close_history(close_df)

    result = calculate_breadth(close_df)
    elapsed = time.time() - started
    latest = result["date"].max()
    print(
        f"완료: {OUTPUT_FILE} / {len(result):,}행 / 최신 {latest:%Y-%m-%d} / {elapsed:.1f}초",
        flush=True,
    )


if __name__ == "__main__":
    main()
