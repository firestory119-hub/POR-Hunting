import os
import time
from datetime import datetime, timedelta

import pandas as pd
from pykrx import stock

DATA_DIR = "data"
CLOSE_FILE = os.path.join(DATA_DIR, "breadth_close_history.csv")
OUTPUT_FILE = os.path.join(DATA_DIR, "breadth_history.csv")
LOOKBACK_CALENDAR_DAYS = 430
MARKETS = ["KOSPI", "KOSDAQ"]

os.makedirs(DATA_DIR, exist_ok=True)


def load_close_history() -> pd.DataFrame:
    if not os.path.exists(CLOSE_FILE):
        return pd.DataFrame(columns=["date", "market", "ticker", "close", "change"])
    df = pd.read_csv(CLOSE_FILE, dtype={"ticker": str})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["ticker"] = df["ticker"].astype(str).str.zfill(6)
    for col in ["close", "change"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["date", "market", "ticker", "close"])


def business_dates(start: str, end: str) -> list[pd.Timestamp]:
    try:
        dates = stock.get_previous_business_days(fromdate=start, todate=end)
        return [pd.Timestamp(d) for d in dates]
    except Exception:
        return list(pd.bdate_range(pd.to_datetime(start), pd.to_datetime(end)))


def fetch_cross_section(date: pd.Timestamp, market: str) -> pd.DataFrame:
    ds = date.strftime("%Y%m%d")
    frame = stock.get_market_ohlcv_by_ticker(ds, market=market)
    if frame is None or frame.empty:
        return pd.DataFrame()

    frame = frame.reset_index()
    ticker_col = frame.columns[0]
    close_col = "종가" if "종가" in frame.columns else None
    change_col = "등락률" if "등락률" in frame.columns else None
    if close_col is None:
        return pd.DataFrame()

    out = pd.DataFrame({
        "date": date,
        "market": market,
        "ticker": frame[ticker_col].astype(str).str.zfill(6),
        "close": pd.to_numeric(frame[close_col], errors="coerce"),
        "change": pd.to_numeric(frame[change_col], errors="coerce") if change_col else 0.0,
    })
    return out[out["close"] > 0].dropna(subset=["close"])


def update_close_history() -> pd.DataFrame:
    old = load_close_history()
    end = datetime.today().date()

    if old.empty:
        start = end - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    else:
        start = old["date"].max().date() + timedelta(days=1)

    if start > end:
        return old

    dates = business_dates(start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
    new_frames = []

    total = len(dates) * len(MARKETS)
    done = 0
    for date in dates:
        for market in MARKETS:
            done += 1
            try:
                frame = fetch_cross_section(date, market)
                if not frame.empty:
                    new_frames.append(frame)
                print(f"[{done}/{total}] {date.date()} {market}: {len(frame)}")
            except Exception as exc:
                print(f"[{done}/{total}] {date.date()} {market} 실패: {exc}")
            time.sleep(0.08)

    if new_frames:
        new = pd.concat(new_frames, ignore_index=True)
        all_df = pd.concat([old, new], ignore_index=True)
    else:
        all_df = old.copy()

    all_df = (
        all_df.drop_duplicates(["date", "market", "ticker"], keep="last")
        .sort_values(["market", "ticker", "date"])
        .reset_index(drop=True)
    )
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
            m[f"ma{window}"] = (
                m.groupby("ticker", group_keys=False)["close"]
                .transform(lambda s: s.rolling(window, min_periods=window).mean())
            )

        m["high_252"] = (
            m.groupby("ticker", group_keys=False)["close"]
            .transform(lambda s: s.rolling(252, min_periods=120).max())
        )
        m["low_252"] = (
            m.groupby("ticker", group_keys=False)["close"]
            .transform(lambda s: s.rolling(252, min_periods=120).min())
        )

        grouped = m.groupby("date", sort=True)
        ad_line = 0
        for date, day in grouped:
            valid_count = len(day)
            if valid_count == 0:
                continue

            advancers = int((day["change"] > 0).sum())
            decliners = int((day["change"] < 0).sum())
            unchanged = int((day["change"] == 0).sum())
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

    out = pd.DataFrame(rows).sort_values(["market", "date"])
    out.to_csv(OUTPUT_FILE, index=False, encoding="utf-8-sig")
    return out


def main():
    close_df = update_close_history()
    if close_df.empty:
        raise RuntimeError("수집된 종가 데이터가 없습니다.")
    result = calculate_breadth(close_df)
    print(f"완료: {OUTPUT_FILE} ({len(result)}행)")


if __name__ == "__main__":
    main()
