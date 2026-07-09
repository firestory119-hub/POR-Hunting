import os
from datetime import datetime, timedelta

import pandas as pd

try:
    from pykrx import stock
except Exception as e:
    raise RuntimeError("pykrx가 설치되어 있어야 합니다. requirements.txt에 pykrx를 추가하세요.") from e


DATA_DIR = "data"
OUT_FILE = os.path.join(DATA_DIR, "market_data.csv")
os.makedirs(DATA_DIR, exist_ok=True)


def get_recent_market_date(max_back_days: int = 14) -> str:
    """
    오늘 포함 최근 거래일을 찾습니다.
    반환 형식: YYYYMMDD
    """
    today = datetime.today()
    for i in range(max_back_days + 1):
        d = (today - timedelta(days=i)).strftime("%Y%m%d")
        try:
            df = stock.get_market_cap_by_ticker(d, market="ALL")
            if df is not None and not df.empty:
                return d
        except Exception:
            pass
    raise RuntimeError("최근 거래일 데이터를 찾지 못했습니다.")


def build_market_csv():
    trade_date = get_recent_market_date()

    cap = stock.get_market_cap_by_ticker(trade_date, market="ALL")
    fund = stock.get_market_fundamental_by_ticker(trade_date, market="ALL")

    if cap is None or cap.empty:
        raise RuntimeError("시가총액 데이터를 가져오지 못했습니다.")

    cap = cap.reset_index().rename(columns={"티커": "종목코드"})
    fund = fund.reset_index().rename(columns={"티커": "종목코드"})

    # pykrx 버전에 따라 컬럼명이 다를 수 있어 방어적으로 처리
    keep_cap_cols = ["종목코드"]
    for c in ["종가", "시가총액", "상장주식수"]:
        if c in cap.columns:
            keep_cap_cols.append(c)
    cap = cap[keep_cap_cols].copy()

    keep_fund_cols = ["종목코드"]
    for c in ["PER", "PBR", "DIV", "EPS", "BPS", "DPS"]:
        if c in fund.columns:
            keep_fund_cols.append(c)
    fund = fund[keep_fund_cols].copy()

    df = cap.merge(fund, on="종목코드", how="left")

    df["종목명"] = df["종목코드"].apply(lambda x: stock.get_market_ticker_name(str(x).zfill(6)))
    df["기준일"] = trade_date

    if "시가총액" in df.columns:
        df["현재시총_억원"] = (pd.to_numeric(df["시가총액"], errors="coerce") / 100_000_000).round(0)

    rename_map = {
        "종가": "현재가",
        "DIV": "배당수익률",
    }
    df = df.rename(columns=rename_map)

    final_cols = [
        "기준일",
        "종목코드",
        "종목명",
        "현재가",
        "현재시총_억원",
        "PER",
        "PBR",
        "배당수익률",
        "EPS",
        "BPS",
        "DPS",
        "상장주식수",
    ]
    final_cols = [c for c in final_cols if c in df.columns]
    df = df[final_cols].copy()

    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)

    # 거래정지/데이터 이상치 방어
    if "현재가" in df.columns:
        df = df[pd.to_numeric(df["현재가"], errors="coerce").fillna(0) > 0]

    df = df.sort_values(["종목코드"]).reset_index(drop=True)
    df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

    print(f"saved: {OUT_FILE}")
    print(f"date: {trade_date}")
    print(f"rows: {len(df)}")


if __name__ == "__main__":
    build_market_csv()
