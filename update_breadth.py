from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
from pykrx import stock


DATA_DIR = Path("data")
BREADTH_HISTORY = DATA_DIR / "breadth_history.csv"
BREADTH_CLOSE_HISTORY = DATA_DIR / "breadth_close_history.csv"

MARKETS = {
    "KOSPI": "1001",
    "KOSDAQ": "2001",
}

LOOKBACK_CALENDAR_DAYS = 430
MAX_WORKERS = int(os.getenv("BREADTH_WORKERS", "12"))
REQUEST_DELAY = float(os.getenv("BREADTH_REQUEST_DELAY", "0.05"))


def previous_business_candidates(days: int = 12) -> list[str]:
    today = datetime.now()
    return [
        (today - timedelta(days=offset)).strftime("%Y%m%d")
        for offset in range(days)
    ]


def resolve_latest_trading_date() -> str:
    for date_text in previous_business_candidates():
        try:
            frame = stock.get_market_ohlcv_by_ticker(
                date_text,
                market="KOSPI",
            )
            if frame is not None and not frame.empty:
                return date_text
        except Exception:
            continue

    raise RuntimeError("최근 거래일을 찾지 못했습니다.")


def get_tickers(market: str, trading_date: str) -> list[str]:
    tickers = stock.get_market_ticker_list(
        trading_date,
        market=market,
    )
    return sorted(set(str(ticker).zfill(6) for ticker in tickers))


def fetch_one_ticker(
    ticker: str,
    start_date: str,
    end_date: str,
) -> dict | None:
    try:
        frame = stock.get_market_ohlcv_by_date(
            start_date,
            end_date,
            ticker,
            adjusted=True,
        )

        if frame is None or frame.empty or "종가" not in frame.columns:
            return None

        close = pd.to_numeric(
            frame["종가"],
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

        high_52w = (
            float(close.tail(250).max())
            if len(close) >= 20
            else latest_close
        )
        low_52w = (
            float(close.tail(250).min())
            if len(close) >= 20
            else latest_close
        )

        return {
            "ticker": ticker,
            "date": close.index[-1],
            "close": latest_close,
            "previous_close": previous_close,
            "advance": latest_close > previous_close,
            "decline": latest_close < previous_close,
            "unchanged": latest_close == previous_close,
            "above_ma20": above_ma(20),
            "above_ma60": above_ma(60),
            "above_ma120": above_ma(120),
            "above_ma200": above_ma(200),
            "new_high_52w": latest_close >= high_52w,
            "new_low_52w": latest_close <= low_52w,
        }

    except Exception:
        return None

    finally:
        if REQUEST_DELAY > 0:
            time.sleep(REQUEST_DELAY)


def fetch_market_rows(
    market: str,
    trading_date: str,
) -> pd.DataFrame:
    tickers = get_tickers(market, trading_date)

    if not tickers:
        return pd.DataFrame()

    end_dt = datetime.strptime(trading_date, "%Y%m%d")
    start_date = (
        end_dt - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    ).strftime("%Y%m%d")

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

    return round(float(valid.astype(bool).mean() * 100), 2)


def fetch_index_close(
    index_code: str,
    trading_date: str,
) -> tuple[float | None, float | None]:
    end_dt = datetime.strptime(trading_date, "%Y%m%d")
    start_date = (
        end_dt - timedelta(days=14)
    ).strftime("%Y%m%d")

    try:
        frame = stock.get_index_ohlcv_by_date(
            start_date,
            trading_date,
            index_code,
        )

        if frame is None or frame.empty:
            return None, None

        close = pd.to_numeric(
            frame["종가"],
            errors="coerce",
        ).dropna()

        if close.empty:
            return None, None

        latest = float(close.iloc[-1])
        previous = (
            float(close.iloc[-2])
            if len(close) >= 2
            else latest
        )
        change_pct = (
            (latest / previous - 1) * 100
            if previous > 0
            else None
        )

        return latest, change_pct

    except Exception:
        return None, None


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

    for column in subset:
        if column in combined.columns:
            combined[column] = combined[column].astype(str)

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
    if history.empty or "ad_line" not in history.columns:
        return 0.0

    rows = history[
        history.get("market", "") == market
    ].copy()

    if rows.empty:
        return 0.0

    values = pd.to_numeric(
        rows["ad_line"],
        errors="coerce",
    ).dropna()

    return float(values.iloc[-1]) if not values.empty else 0.0


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    trading_date = resolve_latest_trading_date()
    date_iso = datetime.strptime(
        trading_date,
        "%Y%m%d",
    ).strftime("%Y-%m-%d")

    print(f"최신 거래일: {date_iso}")

    old_history = load_history(BREADTH_HISTORY)
    summary_rows = []
    close_rows = []

    for market, index_code in MARKETS.items():
        print(f"{market} 수집 시작")
        rows = fetch_market_rows(
            market,
            trading_date,
        )

        if rows.empty:
            print(f"{market}: 수집 결과 없음")
            continue

        advancers = int(rows["advance"].sum())
        decliners = int(rows["decline"].sum())
        unchanged = int(rows["unchanged"].sum())
        ad_net = advancers - decliners

        index_close, index_change_pct = fetch_index_close(
            index_code,
            trading_date,
        )

        ad_line = previous_ad_line(
            old_history,
            market,
        ) + ad_net

        summary_rows.append(
            {
                "date": date_iso,
                "market": market,
                "index_close": index_close,
                "index_change_pct": (
                    round(index_change_pct, 3)
                    if index_change_pct is not None
                    else None
                ),
                "above_ma20": safe_percent(
                    rows["above_ma20"]
                ),
                "above_ma60": safe_percent(
                    rows["above_ma60"]
                ),
                "above_ma120": safe_percent(
                    rows["above_ma120"]
                ),
                "above_ma200": safe_percent(
                    rows["above_ma200"]
                ),
                "advancers": advancers,
                "decliners": decliners,
                "unchanged": unchanged,
                "ad_net": ad_net,
                "ad_line": ad_line,
                "new_high_52w": int(
                    rows["new_high_52w"].sum()
                ),
                "new_low_52w": int(
                    rows["new_low_52w"].sum()
                ),
                "universe_count": int(len(rows)),
                "updated_at": datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
            }
        )

        market_close = rows[
            ["ticker", "close"]
        ].copy()
        market_close.insert(0, "market", market)
        market_close.insert(0, "date", date_iso)
        close_rows.append(market_close)

        print(
            f"{market}: {len(rows)}종목, "
            f"상승 {advancers}, 하락 {decliners}"
        )

    if not summary_rows:
        raise RuntimeError(
            "KOSPI/KOSDAQ Breadth 데이터를 만들지 못했습니다."
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

    invalid = summary_df[
        summary_df[required_columns].isna().any(axis=1)
    ]

    if not invalid.empty:
        raise RuntimeError(
            "필수 Breadth 값에 결측치가 있습니다:\n"
            + invalid[required_columns].to_string(index=False)
        )

    append_deduplicated(
        BREADTH_HISTORY,
        summary_df,
        subset=["market", "date"],
    )

    if close_rows:
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
