import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = "data"
MARKET_DATA = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_DATA = os.path.join(DATA_DIR, "financial_data.csv")
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")

API_KEY = os.environ.get("DART_API_KEY", "").strip()
YEARS = 10

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 POR-Hunting-Financial-Updater/1.0"})


def clean_num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace(" ", "")
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def clean_ticker(x):
    s = str(x).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def load_stock_list():
    if not os.path.exists(MARKET_DATA):
        raise RuntimeError("data/market_data.csv가 없습니다.")

    df = pd.read_csv(MARKET_DATA, dtype=str)

    name_col = "name" if "name" in df.columns else ("종목명" if "종목명" in df.columns else None)
    ticker_col = "ticker" if "ticker" in df.columns else ("종목코드" if "종목코드" in df.columns else None)

    if not name_col or not ticker_col:
        raise RuntimeError("market_data.csv에서 종목명/종목코드 열을 찾지 못했습니다.")

    out = df[[name_col, ticker_col]].copy()
    out.columns = ["name", "ticker"]
    out["ticker"] = out["ticker"].map(clean_ticker)
    return out.dropna().drop_duplicates("ticker")


def get_corp_codes():
    if os.path.exists(CORP_CACHE):
        df = pd.read_csv(CORP_CACHE, dtype=str)
        if not df.empty:
            return df

    if not API_KEY:
        raise RuntimeError("GitHub Secret DART_API_KEY가 없습니다.")

    r = HTTP.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": API_KEY},
        timeout=(5, 30),
    )
    r.raise_for_status()

    z = zipfile.ZipFile(io.BytesIO(r.content))
    root = ET.fromstring(z.read(z.namelist()[0]))

    rows = []
    for item in root.findall("list"):
        ticker = item.findtext("stock_code", "").strip()
        if len(ticker) == 6:
            rows.append({
                "corp_code": item.findtext("corp_code", ""),
                "name": item.findtext("corp_name", ""),
                "ticker": ticker,
            })

    df = pd.DataFrame(rows)
    df.to_csv(CORP_CACHE, index=False, encoding="utf-8-sig")
    return df


def first_number(item):
    for key in ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount"):
        value = clean_num(item.get(key))
        if value is not None:
            return value
    return None


def pick_accounts(items, fs_div):
    revenue, operating, net_income, equity = [], [], [], []

    for item in items:
        acc = str(item.get("account_nm", "")).strip()
        acc_id = str(item.get("account_id", "")).lower().strip()
        sj = str(item.get("sj_div", "")).strip()
        acc_norm = acc.replace(" ", "").replace("\n", "")
        value = first_number(item)

        if value is None:
            continue

        if (not sj) or sj in ("IS", "CIS"):
            if (
                acc_norm in ("매출액", "수익(매출액)", "영업수익", "매출")
                or "매출액" in acc_norm
                or "revenue" in acc_id
                or "sales" in acc_id
            ):
                revenue.append((acc, acc_id, value, fs_div))

            if (
                "영업이익" in acc_norm
                or "operatingincome" in acc_id
                or "operatingprofit" in acc_id
                or "profitlossfromoperatingactivities" in acc_id
            ):
                operating.append((acc, acc_id, value, fs_div))

            if (
                "당기순이익" in acc_norm
                or acc_id in ("ifrs-full_profitloss", "profitloss")
                or "profitlossattributabletoownersofparent" in acc_id
            ):
                net_income.append((acc, acc_id, value, fs_div))

        if (not sj) or sj == "BS":
            if (
                acc_norm in ("자본총계", "자본")
                or "자본총계" in acc_norm
                or acc_id in (
                    "ifrs-full_equity",
                    "ifrs-full_equityattributabletoownersofparent",
                )
            ):
                equity.append((acc, acc_id, value, fs_div))

    def pick(values):
        return min(values, key=lambda x: (len(x[0]), x[0])) if values else None

    exact_equity = [x for x in equity if x[0].replace(" ", "") == "자본총계"]

    return (
        pick(revenue),
        pick(operating),
        pick(net_income),
        exact_equity[0] if exact_equity else pick(equity),
    )


def request_json(url, params):
    try:
        r = HTTP.get(url, params=params, timeout=(5, 15))
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def fetch_one_year(corp_code, year):
    revenue = operating = net_income = equity = None

    for fs_div in ("CFS", "OFS"):
        data = request_json(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
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

        r, o, n, e = pick_accounts(data.get("list", []), fs_div)
        revenue = revenue or r
        operating = operating or o
        net_income = net_income or n
        equity = equity or e

        if revenue and operating and net_income and equity:
            break

    if not (revenue and operating and net_income and equity):
        data = request_json(
            "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
            {
                "crtfc_key": API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": "11011",
            },
        )

        if data.get("status") == "000":
            r, o, n, e = pick_accounts(data.get("list", []), "FALLBACK")
            revenue = revenue or r
            operating = operating or o
            net_income = net_income or n
            equity = equity or e

    return revenue, operating, net_income, equity


def main():
    stocks = load_stock_list()
    corp = get_corp_codes()
    corp["ticker"] = corp["ticker"].map(clean_ticker)

    merged = stocks.merge(corp[["ticker", "corp_code"]], on="ticker", how="left")

    last_year = datetime.today().year - 1
    start_year = last_year - YEARS + 1
    rows = []

    for idx, row in merged.iterrows():
        name = row["name"]
        ticker = row["ticker"]
        corp_code = row.get("corp_code")

        print(f"[{idx + 1}/{len(merged)}] {name} ({ticker})")

        if pd.isna(corp_code):
            continue

        for year in range(start_year, last_year + 1):
            revenue, operating, net_income, equity = fetch_one_year(str(corp_code), year)

            rev_value = revenue[2] if revenue else None
            op_value = operating[2] if operating else None
            net_value = net_income[2] if net_income else None
            eq_value = equity[2] if equity else None

            rows.append({
                "name": name,
                "ticker": ticker,
                "year": year,
                "revenue": rev_value,
                "operating_income": op_value,
                "net_income": net_value,
                "equity": eq_value,
                "operating_margin": (
                    op_value / rev_value * 100
                    if rev_value not in (None, 0) and op_value is not None
                    else None
                ),
                "revenue_account_nm": revenue[0] if revenue else None,
                "op_account_nm": operating[0] if operating else None,
                "net_account_nm": net_income[0] if net_income else None,
                "equity_account_nm": equity[0] if equity else None,
                "fs_div": (
                    operating[3] if operating else
                    revenue[3] if revenue else
                    net_income[3] if net_income else
                    equity[3] if equity else None
                ),
            })

            time.sleep(0.08)

    pd.DataFrame(rows).to_csv(
        FINANCIAL_DATA,
        index=False,
        encoding="utf-8-sig",
    )

    print(f"완료: {FINANCIAL_DATA} / {len(rows)}행")


if __name__ == "__main__":
    main()
