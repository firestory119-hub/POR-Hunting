import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

import pandas as pd
import requests
import FinanceDataReader as fdr


DATA_DIR = "data"
MARKET_FILE = os.path.join(DATA_DIR, "market_data.csv")
HISTORY_FILE = os.path.join(DATA_DIR, "market_history.csv")
FINANCIAL_FILE = os.path.join(DATA_DIR, "financial_data.csv")
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")
API_KEY_FILE = os.path.join(DATA_DIR, "dart_api_key.txt")

INPUT_TICKER_RAW = os.getenv("INPUT_TICKER", "").strip()
INPUT_NAME = os.getenv("INPUT_NAME", "").strip()

YEARS = 10
SLEEP_SECONDS = 0.3

HTTP = requests.Session()
HTTP.headers.update({
    "User-Agent": "Mozilla/5.0 POR-Hunting-One-Stock-Updater/1.0"
})


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


def load_api_key():
    env_key = os.getenv("DART_API_KEY", "").strip()
    if env_key:
        return env_key

    if os.path.exists(API_KEY_FILE):
        try:
            return Path(API_KEY_FILE).read_text(
                encoding="utf-8"
            ).strip()
        except Exception:
            pass

    return ""


API_KEY = load_api_key()


def load_market_data():
    if not os.path.exists(MARKET_FILE):
        raise RuntimeError("data/market_data.csv가 없습니다.")

    market = pd.read_csv(MARKET_FILE, dtype=str)

    rename_map = {}

    if "ticker" in market.columns and "종목코드" not in market.columns:
        rename_map["ticker"] = "종목코드"

    if "name" in market.columns and "종목명" not in market.columns:
        rename_map["name"] = "종목명"

    if "price" in market.columns and "현재가" not in market.columns:
        rename_map["price"] = "현재가"

    if (
        "market_cap_eok" in market.columns
        and "현재시총_억원" not in market.columns
    ):
        rename_map["market_cap_eok"] = "현재시총_억원"

    if rename_map:
        market = market.rename(columns=rename_map)

    required = {"종목코드", "종목명"}

    if not required.issubset(market.columns):
        raise RuntimeError(
            "market_data.csv에 종목코드/종목명 열이 없습니다."
        )

    market["종목코드"] = market["종목코드"].map(clean_ticker)

    for column in [
        "현재가",
        "현재시총_억원",
        "상장주식수",
        "시가총액",
    ]:
        if column in market.columns:
            market[column] = pd.to_numeric(
                market[column],
                errors="coerce",
            )

    if (
        "현재시총_억원" not in market.columns
        and "시가총액" in market.columns
    ):
        market["현재시총_억원"] = (
            market["시가총액"] / 100_000_000
        )

    return (
        market.dropna(subset=["종목코드"])
        .drop_duplicates("종목코드")
        .reset_index(drop=True)
    )


def resolve_target(market):
    ticker = clean_ticker(INPUT_TICKER_RAW)

    if not ticker:
        raise RuntimeError(
            "INPUT_TICKER가 없습니다. 앱에서 수집 요청을 다시 눌러주세요."
        )

    row = market[market["종목코드"] == ticker]

    if row.empty:
        raise RuntimeError(
            f"market_data.csv에서 {ticker}를 찾지 못했습니다."
        )

    name = INPUT_NAME or str(row.iloc[0]["종목명"])

    return ticker, name, row.iloc[0]


def infer_shares(row):
    listed_shares = clean_num(row.get("상장주식수"))

    if listed_shares and listed_shares > 0:
        return listed_shares

    current_price = clean_num(row.get("현재가"))
    current_market_cap_eok = clean_num(
        row.get("현재시총_억원")
    )

    if (
        current_price
        and current_price > 0
        and current_market_cap_eok
        and current_market_cap_eok > 0
    ):
        return (
            current_market_cap_eok
            * 100_000_000
            / current_price
        )

    return None


def read_existing_history():
    if not os.path.exists(HISTORY_FILE):
        return pd.DataFrame()

    try:
        existing = pd.read_csv(
            HISTORY_FILE,
            dtype={"ticker": str},
            parse_dates=["date"],
        )
    except Exception:
        return pd.DataFrame()

    if existing.empty:
        return existing

    existing["ticker"] = existing["ticker"].map(
        clean_ticker
    )

    return existing


def fetch_daily_price(ticker, start_date, end_date):
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
            print(
                f"주가 수집 재시도 {attempt}/3: {exc}",
                flush=True,
            )

            if attempt < 3:
                time.sleep(attempt * 2)

    return pd.DataFrame()


def normalize_price_data(
    price_data,
    name,
    ticker,
    shares,
):
    if price_data is None or price_data.empty:
        return pd.DataFrame()

    price_data = price_data.reset_index()
    date_column = price_data.columns[0]

    if "Close" in price_data.columns:
        close_column = "Close"
    elif "종가" in price_data.columns:
        close_column = "종가"
    else:
        return pd.DataFrame()

    result = pd.DataFrame({
        "name": name,
        "ticker": ticker,
        "date": pd.to_datetime(
            price_data[date_column],
            errors="coerce",
        ),
        "price": pd.to_numeric(
            price_data[close_column],
            errors="coerce",
        ),
    })

    result = result.dropna(
        subset=["date", "price"]
    )
    result = result[result["price"] > 0]

    if result.empty:
        return result

    result["shares"] = float(shares)
    result["market_cap"] = (
        result["price"] * result["shares"]
    )

    return result[
        [
            "name",
            "ticker",
            "date",
            "price",
            "shares",
            "market_cap",
        ]
    ]


def update_daily_history(
    ticker,
    name,
    market_row,
):
    print("=== 1/2 일별 시가총액 수집 ===", flush=True)

    shares = infer_shares(market_row)

    if not shares or shares <= 0:
        raise RuntimeError("상장주식수를 계산하지 못했습니다.")

    existing = read_existing_history()
    today = datetime.today()
    default_start = (
        today - timedelta(days=365 * YEARS + 60)
    )
    start_date = default_start

    if not existing.empty:
        old = existing[existing["ticker"] == ticker]

        if not old.empty:
            last_date = pd.to_datetime(
                old["date"],
                errors="coerce",
            ).max()

            if pd.notna(last_date):
                start_date = max(
                    default_start,
                    last_date.to_pydatetime()
                    - timedelta(days=7),
                )

    price_data = fetch_daily_price(
        ticker,
        start_date,
        today,
    )

    new_rows = normalize_price_data(
        price_data,
        name,
        ticker,
        shares,
    )

    if new_rows.empty:
        raise RuntimeError(
            f"{name}의 일별 주가 데이터를 수집하지 못했습니다."
        )

    frames = []

    if not existing.empty:
        frames.append(existing)

    frames.append(new_rows)

    result = pd.concat(
        frames,
        ignore_index=True,
    )

    result["ticker"] = result["ticker"].map(
        clean_ticker
    )
    result["date"] = pd.to_datetime(
        result["date"],
        errors="coerce",
    )

    for column in [
        "price",
        "shares",
        "market_cap",
    ]:
        result[column] = pd.to_numeric(
            result[column],
            errors="coerce",
        )

    result = (
        result.dropna(
            subset=[
                "ticker",
                "date",
                "price",
                "market_cap",
            ]
        )
        .drop_duplicates(
            subset=["ticker", "date"],
            keep="last",
        )
        .sort_values(["ticker", "date"])
        .reset_index(drop=True)
    )

    result.to_csv(
        HISTORY_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    print(
        f"market_history.csv 완료: "
        f"{ticker} / {len(new_rows):,}일",
        flush=True,
    )

    time.sleep(SLEEP_SECONDS)


def get_corp_codes():
    if os.path.exists(CORP_CACHE):
        try:
            df = pd.read_csv(
                CORP_CACHE,
                dtype=str,
            )

            rename_map = {}

            if (
                "corp_name" in df.columns
                and "name" not in df.columns
            ):
                rename_map["corp_name"] = "name"

            if (
                "stock_code" in df.columns
                and "ticker" not in df.columns
            ):
                rename_map["stock_code"] = "ticker"

            if rename_map:
                df = df.rename(columns=rename_map)

            required = {
                "corp_code",
                "name",
                "ticker",
            }

            if (
                not df.empty
                and required.issubset(df.columns)
            ):
                df["ticker"] = df["ticker"].map(
                    clean_ticker
                )

                return df[
                    ["corp_code", "name", "ticker"]
                ].copy()
        except Exception:
            pass

    if not API_KEY:
        raise RuntimeError(
            "GitHub Secret DART_API_KEY가 없습니다."
        )

    response = HTTP.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": API_KEY},
        timeout=(10, 60),
    )
    response.raise_for_status()

    archive = zipfile.ZipFile(
        io.BytesIO(response.content)
    )
    root = ET.fromstring(
        archive.read(archive.namelist()[0])
    )

    rows = []

    for item in root.findall("list"):
        ticker = item.findtext(
            "stock_code",
            "",
        ).strip()

        if len(ticker) == 6:
            rows.append({
                "corp_code": item.findtext(
                    "corp_code",
                    "",
                ),
                "name": item.findtext(
                    "corp_name",
                    "",
                ),
                "ticker": ticker,
            })

    df = pd.DataFrame(rows)
    df["ticker"] = df["ticker"].map(
        clean_ticker
    )

    df.to_csv(
        CORP_CACHE,
        index=False,
        encoding="utf-8-sig",
    )

    return df[
        ["corp_code", "name", "ticker"]
    ].copy()


def first_number(item):
    for key in (
        "thstrm_amount",
        "thstrm_add_amount",
        "frmtrm_amount",
    ):
        value = clean_num(item.get(key))

        if value is not None:
            return value

    return None


def pick_accounts(items, fs_div):
    revenue = []
    operating = []
    net_income = []
    equity = []

    for item in items:
        account_name = str(
            item.get("account_nm", "")
        ).strip()
        account_id = str(
            item.get("account_id", "")
        ).lower().strip()
        statement = str(
            item.get("sj_div", "")
        ).strip()
        normalized = (
            account_name
            .replace(" ", "")
            .replace("\n", "")
        )
        value = first_number(item)

        if value is None:
            continue

        if (not statement) or statement in ("IS", "CIS"):
            if (
                normalized in (
                    "매출액",
                    "수익(매출액)",
                    "영업수익",
                    "매출",
                )
                or "매출액" in normalized
                or "revenue" in account_id
                or "sales" in account_id
            ):
                revenue.append(
                    (
                        account_name,
                        account_id,
                        value,
                        fs_div,
                    )
                )

            if (
                "영업이익" in normalized
                or "operatingincome" in account_id
                or "operatingprofit" in account_id
                or (
                    "profitlossfromoperatingactivities"
                    in account_id
                )
            ):
                operating.append(
                    (
                        account_name,
                        account_id,
                        value,
                        fs_div,
                    )
                )

            if (
                "당기순이익" in normalized
                or account_id in (
                    "ifrs-full_profitloss",
                    "profitloss",
                )
                or (
                    "profitlossattributabletoownersofparent"
                    in account_id
                )
            ):
                net_income.append(
                    (
                        account_name,
                        account_id,
                        value,
                        fs_div,
                    )
                )

        if (not statement) or statement == "BS":
            if (
                normalized in (
                    "자본총계",
                    "자본",
                )
                or "자본총계" in normalized
                or account_id in (
                    "ifrs-full_equity",
                    (
                        "ifrs-full_"
                        "equityattributabletoownersofparent"
                    ),
                )
            ):
                equity.append(
                    (
                        account_name,
                        account_id,
                        value,
                        fs_div,
                    )
                )

    def pick(values):
        if not values:
            return None

        return min(
            values,
            key=lambda item: (
                len(item[0]),
                item[0],
            ),
        )

    exact_equity = [
        item
        for item in equity
        if item[0].replace(" ", "")
        == "자본총계"
    ]

    return (
        pick(revenue),
        pick(operating),
        pick(net_income),
        (
            exact_equity[0]
            if exact_equity
            else pick(equity)
        ),
    )


def request_json(url, params):
    for attempt in range(1, 4):
        try:
            response = HTTP.get(
                url,
                params=params,
                timeout=(10, 60),
            )
            response.raise_for_status()
            return response.json()

        except Exception as exc:
            print(
                f"DART 재시도 {attempt}/3: {exc}",
                flush=True,
            )

            if attempt < 3:
                time.sleep(attempt * 2)

    return {}


def fetch_one_year(corp_code, year):
    revenue = None
    operating = None
    net_income = None
    equity = None

    for fs_div in ("CFS", "OFS"):
        data = request_json(
            (
                "https://opendart.fss.or.kr/api/"
                "fnlttSinglAcntAll.json"
            ),
            {
                "crtfc_key": API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
                "fs_div": fs_div,
            },
        )

        if data.get("status") != "000":
            continue

        r, o, n, e = pick_accounts(
            data.get("list", []),
            fs_div,
        )

        revenue = revenue or r
        operating = operating or o
        net_income = net_income or n
        equity = equity or e

        if (
            revenue
            and operating
            and net_income
            and equity
        ):
            break

    if not (
        revenue
        and operating
        and net_income
        and equity
    ):
        data = request_json(
            (
                "https://opendart.fss.or.kr/api/"
                "fnlttSinglAcnt.json"
            ),
            {
                "crtfc_key": API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
            },
        )

        if data.get("status") == "000":
            r, o, n, e = pick_accounts(
                data.get("list", []),
                "FALLBACK",
            )

            revenue = revenue or r
            operating = operating or o
            net_income = net_income or n
            equity = equity or e

    return (
        revenue,
        operating,
        net_income,
        equity,
    )


def read_existing_financial():
    if not os.path.exists(FINANCIAL_FILE):
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            FINANCIAL_FILE,
            dtype={"ticker": str},
        )
    except Exception:
        return pd.DataFrame()

    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(
            columns={"종목코드": "ticker"}
        )

    if "종목명" in df.columns and "name" not in df.columns:
        df = df.rename(
            columns={"종목명": "name"}
        )

    if "ticker" in df.columns:
        df["ticker"] = df["ticker"].map(
            clean_ticker
        )

    return df


def update_financial_data(ticker, name):
    print("=== 2/2 연간 재무 수집 ===", flush=True)

    if not API_KEY:
        raise RuntimeError(
            "GitHub Secret DART_API_KEY가 없습니다."
        )

    corp_codes = get_corp_codes()
    corp_row = corp_codes[
        corp_codes["ticker"] == ticker
    ]

    if corp_row.empty:
        raise RuntimeError(
            f"DART corp_code에서 {ticker}를 찾지 못했습니다."
        )

    corp_code = str(
        corp_row.iloc[0]["corp_code"]
    )

    current_year = datetime.today().year
    last_year = current_year - 1
    start_year = last_year - YEARS + 1
    rows = []

    for year in range(start_year, last_year + 1):
        print(
            f"재무 수집: {year}",
            flush=True,
        )

        (
            revenue,
            operating,
            net_income,
            equity,
        ) = fetch_one_year(
            corp_code,
            year,
        )

        revenue_value = (
            revenue[2] if revenue else None
        )
        operating_value = (
            operating[2] if operating else None
        )
        net_value = (
            net_income[2]
            if net_income
            else None
        )
        equity_value = (
            equity[2] if equity else None
        )

        rows.append({
            "name": name,
            "ticker": ticker,
            "year": year,
            "revenue": revenue_value,
            "operating_income": operating_value,
            "net_income": net_value,
            "equity": equity_value,
            "operating_margin": (
                operating_value
                / revenue_value
                * 100
                if (
                    revenue_value not in (None, 0)
                    and operating_value is not None
                )
                else None
            ),
            "revenue_account_nm": (
                revenue[0] if revenue else None
            ),
            "op_account_nm": (
                operating[0]
                if operating
                else None
            ),
            "net_account_nm": (
                net_income[0]
                if net_income
                else None
            ),
            "equity_account_nm": (
                equity[0]
                if equity
                else None
            ),
            "fs_div": (
                operating[3]
                if operating
                else revenue[3]
                if revenue
                else net_income[3]
                if net_income
                else equity[3]
                if equity
                else None
            ),
        })

        time.sleep(0.12)

    new_df = pd.DataFrame(rows)
    existing = read_existing_financial()

    if not existing.empty:
        existing = existing[
            existing["ticker"] != ticker
        ].copy()

        result = pd.concat(
            [existing, new_df],
            ignore_index=True,
        )
    else:
        result = new_df

    result["ticker"] = result["ticker"].map(
        clean_ticker
    )
    result["year"] = pd.to_numeric(
        result["year"],
        errors="coerce",
    )

    result = (
        result.dropna(subset=["ticker", "year"])
        .drop_duplicates(
            subset=["ticker", "year"],
            keep="last",
        )
        .sort_values(["ticker", "year"])
        .reset_index(drop=True)
    )

    result.to_csv(
        FINANCIAL_FILE,
        index=False,
        encoding="utf-8-sig",
    )

    valid_count = int(
        new_df[
            [
                "revenue",
                "operating_income",
                "net_income",
                "equity",
            ]
        ]
        .notna()
        .any(axis=1)
        .sum()
    )

    if valid_count == 0:
        raise RuntimeError(
            f"{name}의 재무 데이터를 DART에서 찾지 못했습니다."
        )

    print(
        f"financial_data.csv 완료: "
        f"{ticker} / {valid_count}개 연도",
        flush=True,
    )


def main():
    os.makedirs(DATA_DIR, exist_ok=True)

    market = load_market_data()
    ticker, name, market_row = resolve_target(
        market
    )

    print(
        f"수집 대상: {name} ({ticker})",
        flush=True,
    )

    update_daily_history(
        ticker,
        name,
        market_row,
    )

    update_financial_data(
        ticker,
        name,
    )

    print(
        "=== 일별 + 재무 수집 완료 ===",
        flush=True,
    )


if __name__ == "__main__":
    main()
