import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd


DATA_DIR = Path("data")
MARKET_FILE = DATA_DIR / "market_data.csv"
ITEMS_JSON = os.getenv("ITEMS_JSON", "[]").strip()
MAX_ITEMS = 10


def clean_ticker(value):
    if value is None:
        return None

    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())

    return digits.zfill(6) if digits else None


def load_market():
    if not MARKET_FILE.exists():
        raise RuntimeError("data/market_data.csv가 없습니다.")

    market = pd.read_csv(MARKET_FILE, dtype=str)

    rename = {}
    if "ticker" in market.columns and "종목코드" not in market.columns:
        rename["ticker"] = "종목코드"
    if "name" in market.columns and "종목명" not in market.columns:
        rename["name"] = "종목명"

    if rename:
        market = market.rename(columns=rename)

    required = {"종목코드", "종목명"}
    if not required.issubset(market.columns):
        raise RuntimeError("market_data.csv에 종목코드/종목명 열이 없습니다.")

    market["종목코드"] = market["종목코드"].map(clean_ticker)

    return market.dropna(subset=["종목코드"]).drop_duplicates("종목코드")


def parse_items():
    try:
        raw = json.loads(ITEMS_JSON)
    except Exception as exc:
        raise RuntimeError(f"ITEMS_JSON 파싱 실패: {exc}") from exc

    if not isinstance(raw, list):
        raise RuntimeError("ITEMS_JSON은 목록이어야 합니다.")

    items = []

    for item in raw:
        if not isinstance(item, dict):
            continue

        ticker = clean_ticker(item.get("ticker"))
        name = str(item.get("name", "")).strip()

        if ticker:
            items.append({"ticker": ticker, "name": name})

    deduped = []
    seen = set()

    for item in items:
        if item["ticker"] in seen:
            continue

        seen.add(item["ticker"])
        deduped.append(item)

    if not deduped:
        raise RuntimeError("수집할 종목이 없습니다.")

    if len(deduped) > MAX_ITEMS:
        raise RuntimeError(
            f"한 번에 최대 {MAX_ITEMS}개까지 수집할 수 있습니다."
        )

    return deduped


def main():
    market = load_market()
    items = parse_items()

    print(f"일괄 수집 대상: {len(items)}개", flush=True)

    success = []
    failed = []

    for index, item in enumerate(items, start=1):
        ticker = item["ticker"]

        market_row = market[market["종목코드"] == ticker]
        if market_row.empty:
            failed.append((ticker, "market_data.csv에 종목 없음"))
            print(
                f"[{index}/{len(items)}] {ticker}: 종목 없음",
                flush=True,
            )
            continue

        name = item["name"] or str(market_row.iloc[0]["종목명"])

        print(
            f"[{index}/{len(items)}] {name} ({ticker}) 수집 시작",
            flush=True,
        )

        env = os.environ.copy()
        env["INPUT_TICKER"] = ticker
        env["INPUT_NAME"] = name

        result = subprocess.run(
            [sys.executable, "update_market_daily.py"],
            env=env,
            check=False,
        )

        if result.returncode == 0:
            success.append((ticker, name))
            print(
                f"[{index}/{len(items)}] {name} 완료",
                flush=True,
            )
        else:
            failed.append((ticker, name))
            print(
                f"[{index}/{len(items)}] {name} 실패",
                flush=True,
            )

    print(
        f"일괄 수집 종료: 성공 {len(success)}개 / 실패 {len(failed)}개",
        flush=True,
    )

    if failed:
        print("실패 목록:", failed, flush=True)

    if not success:
        raise RuntimeError("성공한 종목이 없습니다.")


if __name__ == "__main__":
    main()
