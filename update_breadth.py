from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd


DATA_DIR = Path("data")
BREADTH_HISTORY = DATA_DIR / "breadth_history.csv"
BREADTH_CLOSE_HISTORY = DATA_DIR / "breadth_close_history.csv"

INDEX_SYMBOLS = {
    "KOSPI": "KS11",
    "KOSDAQ": "KQ11",
}

LOOKBACK_CALENDAR_DAYS = 430
MAX_WORKERS = int(os.getenv("BREADTH_WORKERS", "8"))
REQUEST_DELAY = float(os.getenv("BREADTH_REQUEST_DELAY", "0.03"))


def normalize_market(value: str) -> str:
    text = str(value or "").upper().strip()

    if "KOSDAQ" in text:
        return "KOSDAQ"

    if "KOSPI" in text:
        return "KOSPI"

    return ""


def load_listing() -> pd.DataFrame:
    listing = fdr.StockListing("KRX")

    if listing is None or listing.empty:
        raise RuntimeError("FinanceDataReader KRX 종목 목록이 비어 있습니다.")

    code_column = None

    for candidate in ["Code", "Symbol", "종목코드"]:
        if candidate in listing.columns:
            code_column = candidate
            break

    if code_column is None:
        raise RuntimeError(
            f"종목코드 열을 찾지 못했습니다: {listing.columns.tolist()}"
        )

    market_column = None

    for candidate in ["Market", "시장구분", "MarketId"]:
        if candidate in listing.columns:
            market_column = candidate
            break

    if market_column is None:
        raise RuntimeError(
            f"시장 구분 열을 찾지 못했습니다: {listing.columns.tolist()}"
        )

    output = listing[[code_column, market_column]].copy()
    output.columns = ["ticker", "market"]
    output["ticker"] = (
        output["ticker"]
        .astype(str)
        .str.replace(".0", "", regex=False)
        .str.extract(r"(\d+)")[0]
        .str.zfill(6)
    )
    output["market"] = output["market"].map(normalize_market)

    output = output[
        output["market"].isin(["KOSPI", "KOSDAQ"])
        & output["ticker"].str.fullmatch(r"\d{6}", na=False)
    ]

    return (
        output.drop_duplicates(["market", "ticker"])
        .sort_values(["market", "ticker"])
        .reset_index(drop=True)
    )


def load_index_history(
    symbol: str,
    start_date: str,
    end_date: str,
) -> pd.DataFrame:
    frame = fdr.DataReader(symbol, start_date, end_date)

    if frame is None or frame.empty or "Close" not in frame.columns:
        raise RuntimeError(f"{symbol} 지수 데이터를 가져오지 못했습니다.")

    frame = frame.copy()
    frame.index = pd.to_datetime(frame.index, errors="coerce")
    frame["Close"] = pd.to_numeric(frame["Close"], errors="coerce")
    frame = frame.dropna(subset=["Close"]).sort_index()

    if frame.empty:
        raise RuntimeError(f"{symbol} 유효 지수 데이터가 없습니다.")

    return frame


def resolve_latest_trading_date() -> tuple[str, dict[str, pd.DataFrame]]:
    today = datetime.now()
    start_date = (today - timedelta(days=30)).strftime("%Y-%m-%d")
    end_date = today.strftime("%Y-%m-%d")

    index_frames = {
        market: load_index_history(symbol, start_date, end_date)
        for market, symbol in INDEX_SYMBOLS.items()
    }

    latest_dates = [
        frame.index.max()
        for frame in index_frames.values()
        if not frame.empty
    ]

    if not latest_dates:
        raise RuntimeError("최근 거래일을 찾지 못했습니다.")

    latest_date = min(latest_dates)
    return latest_date.strftime("%Y-%m-%d"), index_frames


def fetch_one_ticker(
    ticker: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    try:
        frame = fdr.DataReader(ticker, start_date, end_date)

        if frame is None or frame.empty or "Close" not in frame.columns:
            return None

        close = pd.to_numeric(
            frame["Close"],
            errors="coerce",
        ).dropna()

        close = close[close > 0]

        if len(close) < 20:
            return None

        latest_close = float(close.iloc[-1])
        previous_close = (
            float(close.iloc[-2])
            if len(close) >= 2
            else latest_close
        )

        def above_ma(window: int) -> bool | None:
            if len(close) < window:
                return None

            return latest_close >= float(
                close.tail(window).mean()
            )

        recent_52w = close.tail(250)

        return {
            "ticker": ticker,
            "date": pd.to_datetime(close.index[-1]),
            "close": latest_close,
            "previous_close": previous_close,
            "advance": latest_close > previous_close,
            "decline": latest_close < previous_close,
            "unchanged": latest_close == previous_close,
            "above_ma20": above_ma(20),
            "above_ma60": above_ma(60),
            "above_ma120": above_ma(120),
            "above_ma200": above_ma(200),
            "new_high_52w": latest_close >= float(recent_52w.max()),
            "new_low_52w": latest_close <= float(recent_52w.min()),
        }

    except Exception:
        return None

    finally:
        if REQUEST_DELAY > 0:
            time.sleep(REQUEST_DELAY)


def fetch_market_rows(
    listing: pd.DataFrame,
    market: str,
    trading_date: str,
) -> pd.DataFrame:
    tickers = (
        listing.loc[listing["market"] == market, "ticker"]
        .dropna()
        .astype(str)
        .tolist()
    )

    if not tickers:
        return pd.DataFrame()

    end_dt = datetime.strptime(trading_date, "%Y-%m-%d")
    start_date = (
        end_dt - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    ).strftime("%Y-%m-%d")

    rows: list[dict] = []

    with ThreadPoolExecutor(
        max_workers=MAX_WORKERS
    ) as executor:
        futures = {
            executor.submit(
                fetch_one_ticker,
                ticker,
                start_date,
                trading_date,
            ): ticker
            for ticker in tickers
        }

        for number, future in enumerate(
            as_completed(futures),
            start=1,
        ):
            result = future.result()

            if result:
                rows.append(result)

            if number % 100 == 0:
                print(
                    f"{market}: {number}/{len(tickers)} 처리, "
                    f"성공 {len(rows)}"
                )

    return pd.DataFrame(rows)


def safe_percent(series: pd.Series) -> float | None:
    valid = series.dropna()

    if valid.empty:
        return None

    return round(
        float(valid.astype(bool).mean() * 100),
        2,
    )


def load_history(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()

    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def append_deduplicated(
    path: Path,
    new_rows: pd.DataFrame,
    subset: list[str],
) -> None:
    old = load_history(path)
    combined = pd.concat(
        [old, new_rows],
        ignore_index=True,
    )

    combined = (
        combined.drop_duplicates(
            subset=subset,
            keep="last",
        )
        .sort_values(subset)
        .reset_index(drop=True)
    )

    path.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(
        path,
        index=False,
        encoding="utf-8-sig",
    )


def previous_ad_line(
    history: pd.DataFrame,
    market: str,
) -> float:
    if (
        history.empty
        or "market" not in history.columns
        or "ad_line" not in history.columns
    ):
        return 0.0

    rows = history[
        history["market"].astype(str) == market
    ].copy()

    if rows.empty:
        return 0.0

    values = pd.to_numeric(
        rows["ad_line"],
        errors="coerce",
    ).dropna()

    return float(values.iloc[-1]) if not values.empty else 0.0


def index_values(
    frame: pd.DataFrame,
    trading_date: str,
) -> tuple[float, float]:
    target_date = pd.Timestamp(trading_date)
    usable = frame[frame.index <= target_date].copy()

    if usable.empty:
        raise RuntimeError(f"{trading_date} 지수 데이터가 없습니다.")

    latest = float(usable["Close"].iloc[-1])
    previous = (
        float(usable["Close"].iloc[-2])
        if len(usable) >= 2
        else latest
    )
    change_pct = (
        (latest / previous - 1) * 100
        if previous > 0
        else 0.0
    )

    return latest, change_pct


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    listing = load_listing()
    trading_date, index_frames = resolve_latest_trading_date()

    print(f"최신 거래일: {trading_date}")
    print(f"종목 목록: {len(listing)}개")

    old_history = load_history(BREADTH_HISTORY)
    summary_rows = []
    close_rows = []

    for market in ["KOSPI", "KOSDAQ"]:
        print(f"{market} 수집 시작")

        rows = fetch_market_rows(
            listing,
            market,
            trading_date,
        )

        if rows.empty:
            raise RuntimeError(f"{market} 종목 데이터를 수집하지 못했습니다.")

        rows = rows[
            rows["date"].dt.strftime("%Y-%m-%d")
            == trading_date
        ].copy()

        if rows.empty:
            raise RuntimeError(
                f"{market} 최신 거래일 종목 데이터가 없습니다."
            )

        advancers = int(rows["advance"].sum())
        decliners = int(rows["decline"].sum())
        unchanged = int(rows["unchanged"].sum())
        ad_net = advancers - decliners

        index_close, index_change_pct = index_values(
            index_frames[market],
            trading_date,
        )

        ad_line = previous_ad_line(
            old_history,
            market,
        ) + ad_net

        summary_rows.append(
            {
                "date": trading_date,
                "market": market,
                "index_close": round(index_close, 2),
                "index_change_pct": round(index_change_pct, 3),
                "above_ma20": safe_percent(rows["above_ma20"]),
                "above_ma60": safe_percent(rows["above_ma60"]),
                "above_ma120": safe_percent(rows["above_ma120"]),
                "above_ma200": safe_percent(rows["above_ma200"]),
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
                "ad_net": ad_net,
                "ad_line": ad_line,
                "new_high_52w": int(rows["new_high_52w"].sum()),
                "new_low_52w": int(rows["new_low_52w"].sum()),
                "universe_count": int(len(rows)),
                "updated_at": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )

        market_close = rows[["ticker", "close"]].copy()
        market_close.insert(0, "market", market)
        market_close.insert(0, "date", trading_date)
        close_rows.append(market_close)

        print(
            f"{market}: {len(rows)}종목, "
            f"상승 {advancers}, 하락 {decliners}, "
            f"보합 {unchanged}"
        )

    summary_df = pd.DataFrame(summary_rows)

    required_columns = [
        "date",
        "market",
        "index_close",
        "above_ma20",
        "above_ma60",
        "above_ma120",
        "above_ma200",
        "advancers",
        "decliners",
    ]

    if summary_df[required_columns].isna().any().any():
        raise RuntimeError(
            "최신 Breadth 행에 결측치가 있습니다.\n"
            + summary_df[required_columns].to_string(index=False)
        )

    append_deduplicated(
        BREADTH_HISTORY,
        summary_df,
        subset=["market", "date"],
    )

    close_df = pd.concat(
        close_rows,
        ignore_index=True,
    )

    append_deduplicated(
        BREADTH_CLOSE_HISTORY,
        close_df,
        subset=["market", "ticker", "date"],
    )

    print("Breadth 업데이트 완료")
    print(summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
