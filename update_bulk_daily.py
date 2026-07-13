import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd


DATA_DIR = Path("data")
MARKET_FILE = DATA_DIR / "market_data.csv"
ITEMS_JSON = os.getenv("ITEMS_JSON", "[]").strip()

MAX_ITEMS = 100
BATCH_SIZE = 10
PUSH_RETRIES = 3


def clean_ticker(value):
    if value is None:
        return None

    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())

    return digits.zfill(6) if digits else None


def run(command, check=True):
    return subprocess.run(
        command,
        check=check,
        text=True,
    )


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

    return (
        market.dropna(subset=["종목코드"])
        .drop_duplicates("종목코드")
        .reset_index(drop=True)
    )


def parse_items():
    try:
        raw = json.loads(ITEMS_JSON)
    except Exception as exc:
        raise RuntimeError(f"ITEMS_JSON 파싱 실패: {exc}") from exc

    if not isinstance(raw, list):
        raise RuntimeError("ITEMS_JSON은 목록이어야 합니다.")

    items = []
    seen = set()

    for item in raw:
        if not isinstance(item, dict):
            continue

        ticker = clean_ticker(item.get("ticker"))
        name = str(item.get("name", "")).strip()

        if not ticker or ticker in seen:
            continue

        seen.add(ticker)
        items.append({"ticker": ticker, "name": name})

    if not items:
        raise RuntimeError("수집할 종목이 없습니다.")

    if len(items) > MAX_ITEMS:
        raise RuntimeError(
            f"한 번에 최대 {MAX_ITEMS}개까지 수집할 수 있습니다."
        )

    return items


def commit_and_push(batch_number, batch_items):
    run([
        "git",
        "add",
        "data/market_history.csv",
        "data/financial_data.csv",
        "data/corp_codes.csv",
    ])

    diff = subprocess.run(
        ["git", "diff", "--cached", "--quiet"],
        check=False,
    )

    if diff.returncode == 0:
        print(
            f"{batch_number}차 묶음: 변경된 데이터 없음",
            flush=True,
        )
        return

    names = ", ".join(item["name"] for item in batch_items[:3])
    if len(batch_items) > 3:
        names += f" 외 {len(batch_items) - 3}개"

    run([
        "git",
        "commit",
        "-m",
        f"Bulk batch {batch_number}: {names}",
    ])

    for attempt in range(1, PUSH_RETRIES + 1):
        pushed = subprocess.run(
            ["git", "push", "origin", "HEAD:main"],
            check=False,
        )

        if pushed.returncode == 0:
            print(
                f"{batch_number}차 묶음 저장 성공",
                flush=True,
            )
            return

        print(
            f"{batch_number}차 묶음 push 재시도 "
            f"{attempt}/{PUSH_RETRIES}",
            flush=True,
        )

        run(["git", "fetch", "origin", "main"])

        rebased = subprocess.run(
            ["git", "rebase", "origin/main"],
            check=False,
        )

        if rebased.returncode != 0:
            subprocess.run(
                ["git", "rebase", "--abort"],
                check=False,
            )
            raise RuntimeError(
                "다른 작업과 CSV 충돌이 발생했습니다. "
                "잠시 후 다시 실행해 주세요."
            )

        time.sleep(5)

    raise RuntimeError(
        f"{batch_number}차 묶음을 GitHub에 저장하지 못했습니다."
    )


def main():
    market = load_market()
    items = parse_items()

    run(["git", "config", "user.name", "github-actions[bot]"])
    run([
        "git",
        "config",
        "user.email",
        "41898282+github-actions[bot]@users.noreply.github.com",
    ])

    print(
        f"100개 일괄 수집 시작: 총 {len(items)}개 / "
        f"{BATCH_SIZE}개씩 저장",
        flush=True,
    )

    total_success = 0
    total_failed = []

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start:batch_start + BATCH_SIZE]
        batch_number = batch_start // BATCH_SIZE + 1

        print(
            f"=== {batch_number}차 묶음 "
            f"({batch_start + 1}~{batch_start + len(batch)}) ===",
            flush=True,
        )

        batch_success = []

        for offset, item in enumerate(batch, start=1):
            ticker = item["ticker"]
            market_row = market[market["종목코드"] == ticker]

            if market_row.empty:
                total_failed.append((ticker, "종목 없음"))
                continue

            name = item["name"] or str(market_row.iloc[0]["종목명"])
            item["name"] = name

            current_number = batch_start + offset

            print(
                f"[{current_number}/{len(items)}] "
                f"{name} ({ticker}) 수집 시작",
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
                total_success += 1
                batch_success.append(item)
                print(
                    f"[{current_number}/{len(items)}] {name} 완료",
                    flush=True,
                )
            else:
                total_failed.append((ticker, name))
                print(
                    f"[{current_number}/{len(items)}] {name} 실패",
                    flush=True,
                )

        if batch_success:
            commit_and_push(
                batch_number,
                batch_success,
            )

        print(
            f"{batch_number}차 묶음 종료 · "
            f"누적 성공 {total_success}개 · "
            f"누적 실패 {len(total_failed)}개",
            flush=True,
        )

    print(
        f"전체 종료: 성공 {total_success}개 / "
        f"실패 {len(total_failed)}개",
        flush=True,
    )

    if total_failed:
        print("실패 목록:", total_failed, flush=True)

    if total_success == 0:
        raise RuntimeError("성공한 종목이 없습니다.")


if __name__ == "__main__":
    main()
