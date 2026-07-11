import io
import os
import time
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime

import FinanceDataReader as fdr
import pandas as pd
import requests

DATA_DIR = "data"
MARKET_FILE = os.path.join(DATA_DIR, "market_data.csv")
FINANCIAL_FILE = os.path.join(DATA_DIR, "financial_data.csv")
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
    print("[1/3] market_data.csv 생성")
    df = fdr.StockListing("KRX")
    if df is None or df.empty:
        raise RuntimeError("KRX 종목 목록 조회 실패")

    df["Code"] = df["Code"].astype(str).str.zfill(6)
    df = df.rename(columns={
        "Code": "종목코드",
        "Name": "종목명",
        "Close": "현재가",
        "Marcap": "시가총액",
        "Stocks": "상장주식수",
        "PER": "PER",
        "PBR": "PBR",
        "DIV": "배당수익률",
        "EPS": "EPS",
        "BPS": "BPS",
        "DPS": "DPS",
    })

    df["기준일"] = datetime.today().strftime("%Y-%m-%d")
    if "시가총액" in df.columns:
        df["현재시총_억원"] = (
            pd.to_numeric(df["시가총액"], errors="coerce") / 100_000_000
        ).round(0)

    cols = [
        "기준일", "종목코드", "종목명", "현재가", "현재시총_억원",
        "PER", "PBR", "배당수익률", "EPS", "BPS", "DPS", "상장주식수",
    ]
    df = df[[c for c in cols if c in df.columns]].copy()
    df["종목코드"] = df["종목코드"].map(clean_ticker)

    if "현재가" in df.columns:
        df = df[pd.to_numeric(df["현재가"], errors="coerce").fillna(0) > 0]

    df = df.drop_duplicates("종목코드").sort_values("종목코드").reset_index(drop=True)
    df.to_csv(MARKET_FILE, index=False, encoding="utf-8-sig")
    print(f"market_data.csv 완료: {len(df):,}개")
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

    print("[2/3] DART 종목코드 다운로드")
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
    """
    주요계정 API를 연도당 1회만 호출합니다.
    응답 안에서 연결(CFS)을 우선 선택하고, 없으면 별도(OFS)를 사용합니다.
    """
    data = request_json(
        "https://opendart.fss.or.kr/api/fnlttSinglAcnt.json",
        {
            "crtfc_key": DART_API_KEY,
            "corp_code": corp_code,
            "bsns_year": str(year),
            "reprt_code": "11011",
        },
    )

    if data.get("status") != "000":
        return None, None, None, None

    items = data.get("list", [])

    cfs_items = [x for x in items if str(x.get("fs_div", "")).strip() == "CFS"]
    ofs_items = [x for x in items if str(x.get("fs_div", "")).strip() == "OFS"]

    revenue = operating = net_income = equity = None

    if cfs_items:
        revenue, operating, net_income, equity = pick_accounts(cfs_items, "CFS")

    if not (revenue and operating and net_income and equity) and ofs_items:
        r, o, n, e = pick_accounts(ofs_items, "OFS")
        revenue = revenue or r
        operating = operating or o
        net_income = net_income or n
        equity = equity or e

    return revenue, operating, net_income, equity


def load_financial_targets(market_df):
    """
    전체 2,800여 종목을 매번 조회하지 않고 실제 사용 종목만 수집합니다.

    대상 우선순위:
    1) 기존 data/financial_data.csv에 들어 있는 종목
    2) data/favorites.csv
    3) data/search_history.csv
    """
    targets = []

    if os.path.exists(FINANCIAL_FILE):
        try:
            old = pd.read_csv(FINANCIAL_FILE, dtype=str)
            if {"ticker", "name"}.issubset(old.columns):
                targets.append(old[["ticker", "name"]])
        except Exception:
            pass

    for path, ticker_col, name_col in [
        (os.path.join(DATA_DIR, "favorites.csv"), "ticker", "name"),
        (os.path.join(DATA_DIR, "search_history.csv"), "ticker", "name"),
    ]:
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, dtype=str)
                if {ticker_col, name_col}.issubset(df.columns):
                    part = df[[ticker_col, name_col]].copy()
                    part.columns = ["ticker", "name"]
                    targets.append(part)
            except Exception:
                pass

    if targets:
        result = pd.concat(targets, ignore_index=True)
        result["ticker"] = result["ticker"].map(clean_ticker)
        result["name"] = result["name"].astype(str).str.strip()
        result = result.dropna().drop_duplicates("ticker")
    else:
        result = pd.DataFrame(columns=["ticker", "name"])

    # 이름이나 코드가 오래됐을 수 있으므로 최신 market_data.csv로 보정
    latest = market_df[["종목코드", "종목명"]].copy()
    latest.columns = ["ticker", "latest_name"]
    latest["ticker"] = latest["ticker"].map(clean_ticker)

    if not result.empty:
        result = result.merge(latest, on="ticker", how="left")
        result["name"] = result["latest_name"].fillna(result["name"])
        result = result[["ticker", "name"]]

    return result.drop_duplicates("ticker").reset_index(drop=True)


def build_financial_csv(market_df):
    print("[3/3] financial_data.csv 생성")

    targets = load_financial_targets(market_df)
    if targets.empty:
        raise RuntimeError(
            "재무 수집 대상이 없습니다. 앱에서 종목을 한 번 검색하거나 즐겨찾기에 추가한 뒤 다시 실행하세요."
        )

    print(f"재무 수집 대상: {len(targets):,}개 종목")

    corp = get_corp_codes()
    merged = targets.merge(
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

        print(f"[{idx + 1}/{len(merged)}] {name} ({ticker})", flush=True)

        if pd.isna(corp_code):
            print("  - DART 종목코드 없음, 건너뜀", flush=True)
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
                "operating_margin": (
                    op / rev * 100
                    if rev not in (None, 0) and op is not None
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

            time.sleep(0.03)

    if not rows:
        raise RuntimeError("DART 재무 데이터를 한 건도 수집하지 못했습니다.")

    pd.DataFrame(rows).to_csv(
        FINANCIAL_FILE,
        index=False,
        encoding="utf-8-sig",
    )
    print(f"financial_data.csv 완료: {len(rows):,}행", flush=True)

def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    market_df = build_market_csv()
    build_financial_csv(market_df)
    print("전체 업데이트 완료")


if __name__ == "__main__":
    main()
