import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import pandas as pd
import requests

DATA_DIR = "data"
MARKET_FILE = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_FILE = os.path.join(DATA_DIR, "financial_data.csv")
QUARTERLY_FILE = os.path.join(DATA_DIR, "financial_quarterly.csv")
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")

DART_API_KEY = os.environ.get("DART_API_KEY", "").strip()
YEARS = 10

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 POR-Hunting-Updater/1.0"})


def clean_num(value):
    if value is None:
        return None
    s = str(value).replace(",", "").replace(" ", "").strip()
    if s in ("", "-", "nan", "None"):
        return None
    try:
        return float(s)
    except Exception:
        return None


def clean_ticker(value):
    s = str(value).strip()
    if s.endswith(".0"):
        s = s[:-2]
    return s.zfill(6)


def build_market_csv():
    print("[1/2] 기존 market_data.csv 읽기", flush=True)

    if not os.path.exists(MARKET_FILE):
        raise RuntimeError("data/market_data.csv가 없습니다.")

    df = pd.read_csv(MARKET_FILE, dtype=str)

    if "종목코드" not in df.columns or "종목명" not in df.columns:
        raise RuntimeError("market_data.csv에 종목코드/종목명 열이 없습니다.")

    df["종목코드"] = df["종목코드"].map(clean_ticker)
    print(f"market_data.csv 확인 완료: {len(df):,}개", flush=True)
    return df


def get_corp_codes():
    if not DART_API_KEY:
        raise RuntimeError("GitHub Secret DART_API_KEY가 없습니다.")

    if os.path.exists(CORP_CACHE):
        try:
            df = pd.read_csv(CORP_CACHE, dtype=str).rename(columns={
                "corp_name": "name",
                "stock_code": "ticker",
            })
            if {"corp_code", "name", "ticker"}.issubset(df.columns):
                df["ticker"] = df["ticker"].map(clean_ticker)
                return df[["corp_code", "name", "ticker"]].drop_duplicates("ticker")
        except Exception:
            pass

    print("[DART] 종목코드 다운로드", flush=True)
    r = HTTP.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=(10, 60),
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
    df["ticker"] = df["ticker"].map(clean_ticker)
    df.to_csv(CORP_CACHE, index=False, encoding="utf-8-sig")
    return df


def first_value(item):
    for key in ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount"):
        v = clean_num(item.get(key))
        if v is not None:
            return v
    return None


def pick_accounts(items, fs_div):
    rev, op, net, eq = [], [], [], []

    for it in items:
        acc = str(it.get("account_nm", "")).strip()
        acc_id = str(it.get("account_id", "")).lower().strip()
        sj = str(it.get("sj_div", "")).strip()
        acc_norm = acc.replace(" ", "").replace("\n", "")
        val = first_value(it)
        if val is None:
            continue

        if (not sj) or sj in ("IS", "CIS"):
            if (
                acc_norm in ("매출액", "수익(매출액)", "영업수익", "매출")
                or "매출액" in acc_norm
                or "revenue" in acc_id
                or "sales" in acc_id
            ):
                rev.append((acc, acc_id, val, fs_div))

            if (
                "영업이익" in acc_norm
                or "operatingincome" in acc_id
                or "operatingprofit" in acc_id
                or "profitlossfromoperatingactivities" in acc_id
            ):
                op.append((acc, acc_id, val, fs_div))

            if (
                "당기순이익" in acc_norm
                or acc_id in ("ifrs-full_profitloss", "profitloss")
                or "profitlossattributabletoownersofparent" in acc_id
            ):
                net.append((acc, acc_id, val, fs_div))

        if (not sj) or sj == "BS":
            if (
                acc_norm in ("자본총계", "자본")
                or "자본총계" in acc_norm
                or acc_id in (
                    "ifrs-full_equity",
                    "ifrs-full_equityattributabletoownersofparent",
                )
            ):
                eq.append((acc, acc_id, val, fs_div))

    def pick(lst):
        return min(lst, key=lambda x: (len(x[0]), x[0])) if lst else None

    exact_eq = [x for x in eq if x[0].replace(" ", "") == "자본총계"]
    return pick(rev), pick(op), pick(net), (exact_eq[0] if exact_eq else pick(eq))


def request_json(url, params):
    try:
        r = HTTP.get(url, params=params, timeout=(5, 15))
        r.raise_for_status()
        return r.json()
    except Exception:
        return {}


def fetch_year(corp_code, year):
    revenue = operating = net_income = equity = None

    for fs_div in ("CFS", "OFS"):
        data = request_json(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            {
                "crtfc_key": DART_API_KEY,
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
                "crtfc_key": DART_API_KEY,
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


def build_financial_csv(market_df):
    print("[3/3] financial_data.csv 생성")

    corp = get_corp_codes()
    stocks = market_df[["종목코드", "종목명"]].copy()
    stocks.columns = ["ticker", "name"]
    stocks["ticker"] = stocks["ticker"].map(clean_ticker)

    merged = stocks.merge(
        corp[["ticker", "corp_code"]],
        on="ticker",
        how="left",
    )

    end_year = datetime.today().year - 1
    start_year = end_year - YEARS + 1
    rows = []

    for idx, row in merged.iterrows():
        ticker = row["ticker"]
        name = row["name"]
        corp_code = row.get("corp_code")

        print(f"[{idx + 1}/{len(merged)}] {name} ({ticker})")

        if pd.isna(corp_code):
            continue

        for year in range(start_year, end_year + 1):
            revenue, operating, net_income, equity = fetch_year(str(corp_code), year)

            rev = revenue[2] if revenue else None
            op = operating[2] if operating else None
            net = net_income[2] if net_income else None
            eq = equity[2] if equity else None

            rows.append({
                "name": name,
                "ticker": ticker,
                "year": year,
                "revenue": rev,
                "operating_income": op,
                "net_income": net,
                "equity": eq,
                "operating_margin": (op / rev * 100) if rev not in (None, 0) and op is not None else None,
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
            time.sleep(0.05)

    pd.DataFrame(rows).to_csv(FINANCIAL_FILE, index=False, encoding="utf-8-sig")
    print(f"financial_data.csv 완료: {len(rows):,}행", flush=True)



def fetch_report(corp_code, year, reprt_code):
    data = request_json(
        "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
        {
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": reprt_code,
        },
    )
    if data.get("status") != "000":
        return None, None, None, None, None

    items = data.get("list", [])
    cfs = [x for x in items if str(x.get("fs_div", "")).strip() == "CFS"]
    ofs = [x for x in items if str(x.get("fs_div", "")).strip() == "OFS"]

    revenue = operating = net_income = equity = None
    fs_used = None

    if cfs:
        revenue, operating, net_income, equity = pick_accounts(cfs, "CFS")
        fs_used = "CFS"

    if not (revenue and operating and net_income and equity) and ofs:
        r, o, n, e = pick_accounts(ofs, "OFS")
        revenue = revenue or r
        operating = operating or o
        net_income = net_income or n
        equity = equity or e
        if fs_used is None:
            fs_used = "OFS"

    return revenue, operating, net_income, equity, fs_used


def subtract_value(current, previous):
    if current is None:
        return None
    if previous is None:
        return current
    return current - previous


def build_quarterly_financial_csv(market_df):
    print("[4/4] financial_quarterly.csv 생성", flush=True)

    targets = load_financial_targets(market_df)
    if targets.empty:
        print("분기 재무 수집 대상이 없어 건너뜁니다.", flush=True)
        return

    corp = get_corp_codes()
    merged = targets.merge(corp[["ticker", "corp_code"]], on="ticker", how="left")

    end_year = datetime.today().year
    start_year = end_year - YEARS + 1
    rows = []

    report_map = {
        1: ("11013", "03-31"),
        2: ("11012", "06-30"),
        3: ("11014", "09-30"),
        4: ("11011", "12-31"),
    }

    for idx, row in merged.iterrows():
        ticker = row["ticker"]
        name = row["name"]
        corp_code = row.get("corp_code")

        print(f"[분기 {idx + 1}/{len(merged)}] {name} ({ticker})", flush=True)

        if pd.isna(corp_code):
            continue

        for year in range(start_year, end_year + 1):
            cumulative = {}

            for quarter, (reprt_code, _) in report_map.items():
                revenue, operating, net_income, equity, fs_used = fetch_report(
                    str(corp_code), year, reprt_code
                )
                cumulative[quarter] = {
                    "revenue": revenue[2] if revenue else None,
                    "operating_income": operating[2] if operating else None,
                    "net_income": net_income[2] if net_income else None,
                    "equity": equity[2] if equity else None,
                    "fs_div": fs_used,
                }
                time.sleep(0.03)

            prev = {"revenue": None, "operating_income": None, "net_income": None}

            for quarter, (_, month_day) in report_map.items():
                cur = cumulative[quarter]

                q_revenue = subtract_value(cur["revenue"], prev["revenue"])
                q_operating = subtract_value(cur["operating_income"], prev["operating_income"])
                q_net = subtract_value(cur["net_income"], prev["net_income"])

                if all(v is None for v in [q_revenue, q_operating, q_net, cur["equity"]]):
                    continue

                rows.append({
                    "name": name,
                    "ticker": ticker,
                    "year": year,
                    "quarter": quarter,
                    "period": f"{year}Q{quarter}",
                    "period_end": f"{year}-{month_day}",
                    "revenue": q_revenue,
                    "operating_income": q_operating,
                    "net_income": q_net,
                    "equity": cur["equity"],
                    "operating_margin": (
                        q_operating / q_revenue * 100
                        if q_revenue not in (None, 0) and q_operating is not None
                        else None
                    ),
                    "fs_div": cur["fs_div"],
                })

                for key in ["revenue", "operating_income", "net_income"]:
                    if cur[key] is not None:
                        prev[key] = cur[key]

    pd.DataFrame(rows).to_csv(
        QUARTERLY_FILE,
        index=False,
        encoding="utf-8-sig",
    )
    print(f"financial_quarterly.csv 완료: {len(rows):,}행", flush=True)

def main():
    print("=== 분기 재무 업데이트 시작 ===", flush=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    market_df = build_market_csv()

    print("[2/2] 분기 재무 생성 시작", flush=True)
    build_quarterly_financial_csv(market_df)

    print("=== 분기 재무 업데이트 완료 ===", flush=True)


if __name__ == "__main__":
    main()
