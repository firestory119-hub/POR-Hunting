import os
from datetime import datetime

import pandas as pd
import FinanceDataReader as fdr

DATA_DIR = "data"
OUT_FILE = os.path.join(DATA_DIR, "market_data.csv")

os.makedirs(DATA_DIR, exist_ok=True)


def build_market_csv():
    df = fdr.StockListing("KRX")

    df["Code"] = df["Code"].astype(str).str.zfill(6)

    rename = {
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
    }

    df = df.rename(columns=rename)

    df["기준일"] = datetime.today().strftime("%Y-%m-%d")

    if "시가총액" in df.columns:
        df["현재시총_억원"] = (
            pd.to_numeric(df["시가총액"], errors="coerce") / 100000000
        ).round(0)

    cols = [
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

    cols = [c for c in cols if c in df.columns]
    df = df[cols].copy()

    df["종목코드"] = df["종목코드"].astype(str).str.zfill(6)

    if "현재가" in df.columns:
        df = df[pd.to_numeric(df["현재가"], errors="coerce").fillna(0) > 0]

    df = df.sort_values("종목코드").reset_index(drop=True)

    df.to_csv(OUT_FILE, index=False, encoding="utf-8-sig")

    print(f"saved: {OUT_FILE}")
    print(f"rows: {len(df)}")


if __name__ == "__main__":
    build_market_csv()
