
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
CORP_CACHE = os.path.join(DATA_DIR, "corp_codes.csv")
QUARTERLY_FILE = os.path.join(DATA_DIR, "financial_quarterly.csv")

DART_API_KEY = os.getenv("DART_API_KEY", "").strip()
YEARS = 10

HTTP = requests.Session()
HTTP.headers.update({"User-Agent": "Mozilla/5.0 POR-Hunting-Quarterly/1.0"})


def clean_num(value):
    if value is None:
        return None
    s = str(value).replace(",", "").replace(" ", "").strip()
    if s in {"", "-", "nan", "None"}:
        return None
    try:
        return float(s)
    except Exception:
        return None


def clean_ticker(value):
    if value is None:
        return None
    s = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in s if ch.isdigit())
    return digits.zfill(6) if digits else None


def request_json(url, params, retries=5):
    for attempt in range(retries):
        try:
            response = HTTP.get(url, params=params, timeout=(20, 60))
            response.raise_for_status()
            return response.json()
        except Exception as exc:
            if attempt == retries - 1:
                print(f"API 오류: {exc}", flush=True)
                return {}
            time.sleep(1.5 * (attempt + 1))
    return {}


def get_corp_codes():
    print("[1/4] DART 종목코드 준비", flush=True)

    if os.path.exists(CORP_CACHE):
        try:
            cached = pd.read_csv(CORP_CACHE, dtype=str)
            required = {"corp_code", "stock_code"}
            if required.issubset(cached.columns) and not cached.empty:
                cached["stock_code"] = cached["stock_code"].map(clean_ticker)
                print(f"corp_codes.csv 사용: {len(cached):,}개", flush=True)
                return cached
        except Exception:
            pass

    if not DART_API_KEY:
        raise RuntimeError("GitHub Secret DART_API_KEY가 없습니다.")

    response = HTTP.get(
        "https://opendart.fss.or.kr/api/corpCode.xml",
        params={"crtfc_key": DART_API_KEY},
        timeout=(5, 30),
    )
    response.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        xml_data = zf.read(zf.namelist()[0])

    root = ET.fromstring(xml_data)
    rows = []
    for item in root.findall("list"):
        stock_code = clean_ticker(item.findtext("stock_code", ""))
        if stock_code:
            rows.append({
                "corp_code": item.findtext("corp_code", ""),
                "corp_name": item.findtext("corp_name", ""),
                "stock_code": stock_code,
                "modify_date": item.findtext("modify_date", ""),
            })

    corp = pd.DataFrame(rows)
    corp.to_csv(CORP_CACHE, index=False, encoding="utf-8-sig")
    print(f"corp_codes.csv 생성: {len(corp):,}개", flush=True)
    return corp


def load_targets():
    print("[2/4] 수집 대상 준비", flush=True)
    targets = []

    if os.path.exists(FINANCIAL_FILE):
        try:
            df = pd.read_csv(FINANCIAL_FILE, dtype=str)
            if {"ticker", "name"}.issubset(df.columns):
                targets.append(df[["ticker", "name"]])
        except Exception:
            pass

    for filename in ["favorites.csv", "search_history.csv"]:
        path = os.path.join(DATA_DIR, filename)
        if os.path.exists(path):
            try:
                df = pd.read_csv(path, dtype=str)
                if {"ticker", "name"}.issubset(df.columns):
                    targets.append(df[["ticker", "name"]])
            except Exception:
                pass

    if not targets:
        raise RuntimeError("분기 재무 수집 대상이 없습니다.")

    result = pd.concat(targets, ignore_index=True)
    result["ticker"] = result["ticker"].map(clean_ticker)
    result["name"] = result["name"].astype(str).str.strip()
    result = result.dropna(subset=["ticker"]).drop_duplicates("ticker")

    print(f"수집 대상: {len(result):,}개 종목", flush=True)
    return result.reset_index(drop=True)


def first_number(item):
    for key in ("thstrm_amount", "thstrm_add_amount", "frmtrm_amount"):
        value = clean_num(item.get(key))
        if value is not None:
            return value
    return None


def pick_accounts(items, fs_div_label):
    revenue_candidates = []
    operating_candidates = []
    net_candidates = []
    equity_candidates = []

    for item in items:
        account_name = str(item.get("account_nm", "")).strip()
        account_id = str(item.get("account_id", "")).strip().lower()
        statement = str(item.get("sj_div", "")).strip()
        norm = account_name.replace(" ", "").replace("\n", "")
        value = first_number(item)
        if value is None:
            continue

        is_income = not statement or statement in {"IS", "CIS"}
        is_balance = not statement or statement == "BS"

        if is_income:
            if norm in {"매출액", "수익(매출액)", "영업수익"} or "매출액" in norm or "revenue" in account_id or "sales" in account_id:
                revenue_candidates.append((account_name, account_id, value, fs_div_label))
            if "영업이익" in norm or "operatingincome" in account_id or "operatingprofit" in account_id or "profitlossfromoperatingactivities" in account_id:
                operating_candidates.append((account_name, account_id, value, fs_div_label))
            if "당기순이익" in norm or "분기순이익" in norm or account_id in {"ifrs-full_profitloss", "profitloss"} or "profitlossattributabletoownersofparent" in account_id:
                net_candidates.append((account_name, account_id, value, fs_div_label))

        if is_balance:
            if norm in {"자본총계", "자본"} or "자본총계" in norm or account_id in {"ifrs-full_equity", "ifrs-full_equityattributabletoownersofparent"}:
                equity_candidates.append((account_name, account_id, value, fs_div_label))

    def choose(candidates, exact_name=None):
        if not candidates:
            return None
        if exact_name:
            exact = [x for x in candidates if x[0].replace(" ", "") == exact_name]
            if exact:
                return exact[0]
        return min(candidates, key=lambda x: (len(x[0]), x[0]))

    return (
        choose(revenue_candidates),
        choose(operating_candidates),
        choose(net_candidates),
        choose(equity_candidates, "자본총계"),
    )


def fetch_report(corp_code, year, report_code):
    for fs_div in ("CFS", "OFS"):
        data = request_json(
            "https://opendart.fss.or.kr/api/fnlttSinglAcntAll.json",
            {
                "crtfc_key": DART_API_KEY,
                "corp_code": corp_code,
                "bsns_year": str(year),
                "reprt_code": report_code,
                "fs_div": fs_div,
            },
        )
        if data.get("status") == "000" and data.get("list"):
            revenue, operating, net_income, equity = pick_accounts(data["list"], fs_div)
            return {
                "revenue": revenue[2] if revenue else None,
                "operating_income": operating[2] if operating else None,
                "net_income": net_income[2] if net_income else None,
                "equity": equity[2] if equity else None,
                "fs_div": fs_div,
            }
    return None


def subtract_current(current, previous):
    if current is None:
        return None
    if previous is None:
        return current
    return current - previous


def main():
    print("=== 분기 재무 업데이트 시작 ===", flush=True)

    if not DART_API_KEY:
        raise RuntimeError("GitHub Secret DART_API_KEY가 없습니다.")

    os.makedirs(DATA_DIR, exist_ok=True)
    targets = load_targets()
    corp = get_corp_codes()

    merged = targets.merge(
        corp[["corp_code", "stock_code"]],
        left_on="ticker",
        right_on="stock_code",
        how="left",
    )

    report_map = {
        1: ("11013", "03-31"),
        2: ("11012", "06-30"),
        3: ("11014", "09-30"),
        4: ("11011", "12-31"),
    }

    current_year = datetime.today().year
    start_year = current_year - YEARS + 1
    rows = []

    print("[3/4] DART 분기 데이터 수집", flush=True)

    for idx, row in merged.iterrows():
        ticker = row["ticker"]
        name = row["name"]
        corp_code = row.get("corp_code")

        print(f"[{idx + 1}/{len(merged)}] {name} ({ticker})", flush=True)

        if pd.isna(corp_code):
            print("  - DART corp_code 없음, 건너뜀", flush=True)
            continue

        for year in range(start_year, current_year + 1):
            cumulative = {}

            for quarter, (report_code, _) in report_map.items():
                cumulative[quarter] = fetch_report(str(corp_code), year, report_code)
                time.sleep(0.5)

            previous = {"revenue": None, "operating_income": None, "net_income": None}

            for quarter, (_, month_day) in report_map.items():
                current = cumulative.get(quarter)
                if not current:
                    continue

                q_revenue = subtract_current(current["revenue"], previous["revenue"])
                q_operating = subtract_current(current["operating_income"], previous["operating_income"])
                q_net = subtract_current(current["net_income"], previous["net_income"])

                if all(v is None for v in [q_revenue, q_operating, q_net, current["equity"]]):
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
                    "equity": current["equity"],
                    "operating_margin": (
                        q_operating / q_revenue * 100
                        if q_revenue not in (None, 0) and q_operating is not None
                        else None
                    ),
                    "fs_div": current["fs_div"],
                })

                for key in previous:
                    if current[key] is not None:
                        previous[key] = current[key]

    print("[4/4] CSV 저장", flush=True)

    if not rows:
        raise RuntimeError("분기 재무 데이터를 한 건도 수집하지 못했습니다.")

    result_df = pd.DataFrame(rows)
    result_df.to_csv(QUARTERLY_FILE, index=False, encoding="utf-8-sig")

    print(f"financial_quarterly.csv 완료: {len(result_df):,}행", flush=True)
    print("=== 분기 재무 업데이트 완료 ===", flush=True)


if __name__ == "__main__":
    main()
