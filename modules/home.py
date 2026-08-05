import os
import re
from datetime import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


DATA_DIR = "data"
FAVORITES_FILE = os.path.join(DATA_DIR, "favorites.csv")
MARKET_DATA_CSV = os.path.join(DATA_DIR, "market_data.csv")
MARKET_HISTORY_CSV = os.path.join(DATA_DIR, "market_history.csv")
FINANCIAL_DATA_CSV = os.path.join(DATA_DIR, "financial_data.csv")
CONSENSUS_XLSX = os.path.join(DATA_DIR, "consensus.xlsx")


def _clean_ticker(value) -> str:
    text = str(value or "").strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


def _session_or_csv(path: str, columns: list[str]) -> pd.DataFrame:
    state_key = f"_session_{os.path.basename(path)}"

    if state_key in st.session_state:
        df = st.session_state[state_key].copy()
    elif os.path.exists(path):
        try:
            df = pd.read_csv(path, dtype=str)
        except Exception:
            df = pd.DataFrame(columns=columns)
    else:
        df = pd.DataFrame(columns=columns)

    for column in columns:
        if column not in df.columns:
            df[column] = ""

    return df[columns].copy()


@st.cache_data(show_spinner=False, ttl=300)
def _load_market() -> pd.DataFrame:
    if not os.path.exists(MARKET_DATA_CSV):
        return pd.DataFrame()

    try:
        df = pd.read_csv(MARKET_DATA_CSV, dtype=str)
    except Exception:
        return pd.DataFrame()

    rename_map = {}

    if "name" in df.columns and "종목명" not in df.columns:
        rename_map["name"] = "종목명"
    if "ticker" in df.columns and "종목코드" not in df.columns:
        rename_map["ticker"] = "종목코드"
    if "price" in df.columns and "현재가" not in df.columns:
        rename_map["price"] = "현재가"
    if "market_cap_eok" in df.columns and "현재시총_억원" not in df.columns:
        rename_map["market_cap_eok"] = "현재시총_억원"

    if rename_map:
        df = df.rename(columns=rename_map)

    if not {"종목명", "종목코드"}.issubset(df.columns):
        return pd.DataFrame()

    df["종목코드"] = df["종목코드"].map(_clean_ticker)

    for column in ["현재가", "현재시총_억원", "시가총액"]:
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")

    if "현재시총_억원" not in df.columns and "시가총액" in df.columns:
        df["현재시총_억원"] = df["시가총액"] / 100_000_000

    return df.drop_duplicates("종목코드").reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=300)
def _load_financials() -> pd.DataFrame:
    if not os.path.exists(FINANCIAL_DATA_CSV):
        return pd.DataFrame()

    try:
        df = pd.read_csv(FINANCIAL_DATA_CSV, dtype={"ticker": str})
    except Exception:
        return pd.DataFrame()

    if "종목코드" in df.columns and "ticker" not in df.columns:
        df = df.rename(columns={"종목코드": "ticker"})

    if "종목명" in df.columns and "name" not in df.columns:
        df = df.rename(columns={"종목명": "name"})

    if "ticker" not in df.columns or "year" not in df.columns:
        return pd.DataFrame()

    df["ticker"] = df["ticker"].map(_clean_ticker)
    df["year"] = pd.to_numeric(df["year"], errors="coerce")
    df["operating_income"] = pd.to_numeric(
        df.get("operating_income"),
        errors="coerce",
    )

    return df


@st.cache_data(show_spinner=False, ttl=300)
def _load_consensus() -> pd.DataFrame:
    columns = [
        "name",
        "ticker",
        "year",
        "operating_income_eok",
        "target_por",
        "updated_at",
    ]

    if not os.path.exists(CONSENSUS_XLSX):
        return pd.DataFrame(columns=columns)

    try:
        wide = pd.read_excel(
            CONSENSUS_XLSX,
            sheet_name="컨센서스입력",
            header=1,
            dtype={"종목코드": str},
            engine="openpyxl",
        )
    except Exception:
        return pd.DataFrame(columns=columns)

    if not {"종목명", "종목코드"}.issubset(wide.columns):
        return pd.DataFrame(columns=columns)

    year_columns = [
        column
        for column in wide.columns
        if re.fullmatch(r"\d{4}E?", str(column).strip())
    ]

    if not year_columns:
        return pd.DataFrame(columns=columns)

    id_columns = [
        column
        for column in [
            "종목명",
            "종목코드",
            "목표POR",
            "업데이트일",
        ]
        if column in wide.columns
    ]

    long_df = wide.melt(
        id_vars=id_columns,
        value_vars=year_columns,
        var_name="year",
        value_name="operating_income_eok",
    ).rename(
        columns={
            "종목명": "name",
            "종목코드": "ticker",
            "목표POR": "target_por",
            "업데이트일": "updated_at",
        }
    )

    long_df["ticker"] = long_df["ticker"].map(_clean_ticker)
    long_df["year"] = pd.to_numeric(
        long_df["year"].astype(str).str.extract(r"(\d{4})")[0],
        errors="coerce",
    )
    long_df["operating_income_eok"] = pd.to_numeric(
        long_df["operating_income_eok"],
        errors="coerce",
    )

    if "target_por" not in long_df.columns:
        long_df["target_por"] = None
    if "updated_at" not in long_df.columns:
        long_df["updated_at"] = None

    long_df["target_por"] = pd.to_numeric(
        long_df["target_por"],
        errors="coerce",
    )
    long_df["updated_at"] = pd.to_datetime(
        long_df["updated_at"],
        errors="coerce",
    )

    return long_df.dropna(
        subset=["ticker", "year", "operating_income_eok"]
    ).reset_index(drop=True)


@st.cache_data(show_spinner=False, ttl=300)
def _load_latest_history(tickers: tuple[str, ...]) -> pd.DataFrame:
    columns = ["ticker", "date", "price", "market_cap"]

    if not tickers or not os.path.exists(MARKET_HISTORY_CSV):
        return pd.DataFrame(columns=columns)

    try:
        df = pd.read_csv(
            MARKET_HISTORY_CSV,
            usecols=columns,
            dtype={"ticker": str},
        )
    except Exception:
        return pd.DataFrame(columns=columns)

    df["ticker"] = df["ticker"].map(_clean_ticker)
    df = df[df["ticker"].isin(tickers)].copy()
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df["price"] = pd.to_numeric(df["price"], errors="coerce")
    df["market_cap"] = pd.to_numeric(df["market_cap"], errors="coerce")

    return (
        df.dropna(subset=["ticker", "date", "market_cap"])
        .sort_values("date")
        .drop_duplicates("ticker", keep="last")
        .reset_index(drop=True)
    )


def _latest_positive_actual(
    financials: pd.DataFrame,
    ticker: str,
) -> tuple[int | None, float | None]:
    rows = financials[
        (financials["ticker"] == ticker)
        & financials["operating_income"].notna()
        & (financials["operating_income"] > 0)
    ].sort_values("year")

    if rows.empty:
        return None, None

    row = rows.iloc[-1]

    return (
        int(row["year"]),
        float(row["operating_income"]) / 100_000_000,
    )


def _alpha_score(
    current_por: float | None,
    expected_por: float | None,
    upside: float | None,
    growth: float | None,
) -> float:
    score = 0.0

    if current_por is not None:
        score += max(0, min(30, (15 - current_por) * 2))

    if expected_por is not None:
        score += max(0, min(30, (12 - expected_por) * 3))

    if upside is not None:
        score += max(0, min(25, upside / 4))

    if growth is not None:
        score += max(0, min(15, growth / 6))

    return round(max(0, min(100, score)), 1)


def _signal(score: float) -> str:
    if score >= 85:
        return "🟢 Strong Buy"
    if score >= 70:
        return "🟢 Buy"
    if score >= 55:
        return "🟡 Watch"
    if score >= 40:
        return "🟠 Neutral"
    return "🔴 Caution"


def _stars(score: float) -> str:
    count = max(1, min(5, int((score + 19.999) // 20)))
    return "★" * count + "☆" * (5 - count)


def _build_home_radar(
    favorites: pd.DataFrame,
    market: pd.DataFrame,
    financials: pd.DataFrame,
    consensus: pd.DataFrame,
    latest_history: pd.DataFrame,
) -> pd.DataFrame:
    if favorites.empty or market.empty or financials.empty:
        return pd.DataFrame()

    available_years = sorted(
        consensus["year"].dropna().astype(int).unique().tolist()
    )
    selected_year = (
        available_years[0]
        if available_years
        else datetime.today().year
    )

    rows = []

    for _, favorite in favorites.iterrows():
        ticker = _clean_ticker(favorite["ticker"])

        market_rows = market[
            market["종목코드"] == ticker
        ]

        if market_rows.empty:
            continue

        market_row = market_rows.iloc[0]
        name = str(
            market_row.get(
                "종목명",
                favorite.get("name", ticker),
            )
        )

        history_rows = latest_history[
            latest_history["ticker"] == ticker
        ]

        if not history_rows.empty:
            latest = history_rows.iloc[-1]
            current_price = float(latest["price"])
            current_mcap_eok = (
                float(latest["market_cap"]) / 100_000_000
            )
            base_date = latest["date"]
        else:
            current_price = pd.to_numeric(
                market_row.get("현재가"),
                errors="coerce",
            )
            current_mcap_eok = pd.to_numeric(
                market_row.get("현재시총_억원"),
                errors="coerce",
            )
            base_date = pd.NaT

        actual_year, actual_oi = _latest_positive_actual(
            financials,
            ticker,
        )

        current_por = None

        if (
            pd.notna(current_mcap_eok)
            and current_mcap_eok > 0
            and actual_oi
            and actual_oi > 0
        ):
            current_por = current_mcap_eok / actual_oi

        consensus_rows = consensus[
            (consensus["ticker"] == ticker)
            & (consensus["year"] == selected_year)
        ]

        expected_oi = None
        target_por = 8.0
        updated_at = pd.NaT

        if not consensus_rows.empty:
            consensus_row = consensus_rows.iloc[-1]
            expected_oi = float(
                consensus_row["operating_income_eok"]
            )

            saved_target = pd.to_numeric(
                consensus_row.get("target_por"),
                errors="coerce",
            )

            if pd.notna(saved_target) and saved_target > 0:
                target_por = float(saved_target)

            updated_at = consensus_row.get("updated_at")

        expected_por = None
        target_price = None
        upside = None
        growth = None

        if (
            expected_oi
            and expected_oi > 0
            and pd.notna(current_mcap_eok)
            and current_mcap_eok > 0
        ):
            expected_por = current_mcap_eok / expected_oi
            target_mcap = expected_oi * target_por

            if pd.notna(current_price) and current_price > 0:
                target_price = (
                    current_price
                    * target_mcap
                    / current_mcap_eok
                )
                upside = (
                    target_price / current_price - 1
                ) * 100

        if expected_oi and actual_oi and actual_oi > 0:
            growth = (expected_oi / actual_oi - 1) * 100

        score = _alpha_score(
            current_por,
            expected_por,
            upside,
            growth,
        )

        reasons = []

        if expected_por is not None:
            reasons.append(
                f"{selected_year}E POR {expected_por:.2f}배"
            )
        if upside is not None:
            reasons.append(
                f"상승여력 {upside:+.1f}%"
            )
        if growth is not None:
            reasons.append(
                f"영업이익 성장 {growth:+.1f}%"
            )

        rows.append(
            {
                "종목명": name,
                "종목코드": ticker,
                "기준일": base_date,
                "현재가": current_price,
                "현재 POR": current_por,
                f"{selected_year}E POR": expected_por,
                "목표 주가": target_price,
                "상승여력(%)": upside,
                "영업이익 성장률(%)": growth,
                "Alpha Score": score,
                "Alpha": _stars(score),
                "Signal": _signal(score),
                "추천 이유": " · ".join(reasons),
                "컨센서스 수정일": updated_at,
                "컨센서스 연도": selected_year,
            }
        )

    if not rows:
        return pd.DataFrame()

    return (
        pd.DataFrame(rows)
        .sort_values(
            ["Alpha Score", "상승여력(%)"],
            ascending=[False, False],
            na_position="last",
        )
        .reset_index(drop=True)
    )


def _load_breadth_summary(
    breadth_history_csv: str,
) -> dict:
    default = {
        "market": "KOSPI",
        "date": None,
        "index_close": None,
        "score": None,
        "signal": "데이터 없음",
        "above_ma20": None,
        "above_ma200": None,
    }

    if not breadth_history_csv or not os.path.exists(
        breadth_history_csv
    ):
        return default

    try:
        df = pd.read_csv(breadth_history_csv)
    except Exception:
        return default

    if df.empty or "date" not in df.columns:
        return default

    df["date"] = pd.to_datetime(
        df["date"],
        errors="coerce",
    )

    for column in [
        "index_close",
        "above_ma20",
        "above_ma60",
        "above_ma120",
        "above_ma200",
        "ad_net",
    ]:
        if column in df.columns:
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    market = (
        "KOSPI"
        if "market" in df.columns
        and (df["market"] == "KOSPI").any()
        else str(df.get("market", pd.Series(["시장"])).iloc[-1])
    )

    market_df = (
        df[df["market"] == market].copy()
        if "market" in df.columns
        else df.copy()
    )

    market_df = market_df.dropna(subset=["date"]).sort_values(
        "date"
    )

    if market_df.empty:
        return default

    latest = market_df.iloc[-1]

    weights = {
        "above_ma20": 0.35,
        "above_ma60": 0.25,
        "above_ma120": 0.20,
        "above_ma200": 0.20,
    }

    weighted_score = 0.0
    used_weight = 0.0

    for column, weight in weights.items():
        value = pd.to_numeric(
            latest.get(column),
            errors="coerce",
        )

        if pd.notna(value):
            weighted_score += (
                max(0.0, min(100.0, float(value)))
                * weight
            )
            used_weight += weight

    score = (
        round(weighted_score / used_weight, 1)
        if used_weight > 0
        else None
    )

    ad_net = pd.to_numeric(
        latest.get("ad_net"),
        errors="coerce",
    )
    above_200 = pd.to_numeric(
        latest.get("above_ma200"),
        errors="coerce",
    )

    if score is None:
        signal = "데이터 확인"
    elif score >= 70 and (
        pd.isna(ad_net) or ad_net >= 0
    ):
        signal = "🟢 Risk ON"
    elif score <= 30 and (
        pd.isna(ad_net) or ad_net < 0
    ):
        signal = "🔴 Risk OFF"
    elif score <= 40 and (
        pd.isna(above_200) or above_200 <= 25
    ):
        signal = "🟡 바닥 탐색"
    else:
        signal = "🟠 중립"

    return {
        "market": market,
        "date": latest.get("date"),
        "index_close": latest.get("index_close"),
        "score": score,
        "signal": signal,
        "above_ma20": latest.get("above_ma20"),
        "above_ma200": latest.get("above_ma200"),
    }


def _open_stock(stock_name: str):
    st.session_state["stock_query"] = stock_name
    st.session_state["main_page_selector"] = "📊 종목 분석"
    st.query_params["collecting_name"] = stock_name
    st.rerun()


def render_home_page(breadth_history_csv: str):
    st.title("🏠 POR Alpha Home")
    st.caption(
        "오늘 확인할 종목과 시장 상태를 한 화면에서 확인합니다."
    )

    favorites = _session_or_csv(
        FAVORITES_FILE,
        ["name", "ticker", "saved_at"],
    )

    favorites["ticker"] = favorites["ticker"].map(
        _clean_ticker
    )
    favorites = favorites.drop_duplicates(
        "ticker",
        keep="last",
    )

    market = _load_market()
    financials = _load_financials()
    consensus = _load_consensus()
    latest_history = _load_latest_history(
        tuple(favorites["ticker"].dropna().tolist())
    )

    radar = _build_home_radar(
        favorites,
        market,
        financials,
        consensus,
        latest_history,
    )

    breadth = _load_breadth_summary(
        breadth_history_csv
    )

    strong_count = 0
    average_alpha = None
    consensus_count = 0
    stale_count = 0

    if not radar.empty:
        average_alpha = radar["Alpha Score"].mean()
        strong_count = radar["Signal"].isin(
            ["🟢 Strong Buy", "🟢 Buy"]
        ).sum()
        consensus_count = radar[
            "컨센서스 수정일"
        ].notna().sum()

        stale_limit = pd.Timestamp.today().normalize() - pd.Timedelta(
            days=30
        )
        stale_count = (
            radar["컨센서스 수정일"].notna()
            & (
                pd.to_datetime(
                    radar["컨센서스 수정일"],
                    errors="coerce",
                )
                < stale_limit
            )
        ).sum()

    summary_columns = st.columns(5)

    summary_columns[0].metric(
        breadth["market"],
        (
            f"{breadth['index_close']:,.2f}"
            if pd.notna(breadth["index_close"])
            else "-"
        ),
    )
    summary_columns[1].metric(
        "Market Breadth",
        (
            f"{breadth['score']:.1f}/100"
            if breadth["score"] is not None
            else "-"
        ),
    )
    summary_columns[2].metric(
        "시장 상태",
        breadth["signal"],
    )
    summary_columns[3].metric(
        "평균 Alpha",
        (
            f"{average_alpha:.1f}점"
            if average_alpha is not None
            else "-"
        ),
    )
    summary_columns[4].metric(
        "Strong/Buy",
        f"{strong_count}개",
    )

    if breadth["date"] is not None:
        st.caption(
            f"시장 기준일: {pd.Timestamp(breadth['date']):%Y-%m-%d}"
        )

    st.divider()

    st.subheader("⭐ 오늘의 Alpha TOP 5")

    if radar.empty:
        st.warning(
            "즐겨찾기 또는 분석 데이터가 없어 TOP5를 계산하지 못했습니다."
        )
    else:
        top5 = radar.head(5)
        card_columns = st.columns(len(top5))

        for index, (_, row) in enumerate(top5.iterrows()):
            with card_columns[index]:
                st.markdown(
                    f"""
                    <div style="
                        border:1px solid #e9ecef;
                        border-radius:16px;
                        padding:16px;
                        min-height:260px;
                        background:#ffffff;
                        box-shadow:0 3px 12px rgba(0,0,0,0.06);
                    ">
                        <div style="font-size:20px;font-weight:800;">
                            {row['종목명']}
                        </div>
                        <div style="font-size:18px;margin:6px 0;">
                            {row['Alpha']}
                        </div>
                        <div style="
                            display:inline-block;
                            padding:5px 10px;
                            border-radius:999px;
                            background:#e9f5db;
                            color:#386641;
                            font-weight:700;
                            margin-bottom:10px;
                        ">
                            {row['Signal']}
                        </div>
                        <div style="font-size:26px;font-weight:800;">
                            {row['Alpha Score']:.1f}점
                        </div>
                        <div style="margin-top:8px;">
                            현재 POR:
                            <b>{
                                f"{row['현재 POR']:.2f}배"
                                if pd.notna(row['현재 POR'])
                                else "-"
                            }</b>
                        </div>
                        <div style="margin-top:6px;">
                            상승여력:
                            <b>{
                                f"{row['상승여력(%)']:+.1f}%"
                                if pd.notna(row['상승여력(%)'])
                                else "-"
                            }</b>
                        </div>
                        <div style="
                            margin-top:10px;
                            color:#6c757d;
                            font-size:12px;
                            line-height:1.45;
                        ">
                            {row['추천 이유']}
                        </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

                if st.button(
                    "차트 보기",
                    key=f"home_stock_{row['종목코드']}",
                    use_container_width=True,
                ):
                    _open_stock(str(row["종목명"]))

    st.divider()

    left, right = st.columns([1.35, 1])

    with left:
        st.subheader("📊 Alpha TOP 10")

        if not radar.empty:
            top10 = radar.head(10).sort_values(
                "Alpha Score",
                ascending=True,
            )

            figure = go.Figure(
                go.Bar(
                    x=top10["Alpha Score"],
                    y=top10["종목명"],
                    orientation="h",
                    text=top10["Alpha Score"].map(
                        lambda value: f"{value:.1f}점"
                    ),
                    textposition="auto",
                    customdata=top10[
                        [
                            "현재 POR",
                            "상승여력(%)",
                            "Signal",
                        ]
                    ],
                    hovertemplate=(
                        "<b>%{y}</b><br>"
                        "Alpha: %{x:.1f}점<br>"
                        "현재 POR: %{customdata[0]:.2f}배<br>"
                        "상승여력: %{customdata[1]:.1f}%<br>"
                        "%{customdata[2]}"
                        "<extra></extra>"
                    ),
                )
            )

            figure.update_layout(
                height=430,
                margin=dict(l=20, r=20, t=20, b=30),
                xaxis_title="Alpha Score",
                yaxis_title="",
            )

            st.plotly_chart(
                figure,
                use_container_width=True,
            )
        else:
            st.info("Alpha Radar 데이터가 없습니다.")

    with right:
        st.subheader("🔥 오늘 체크할 항목")

        if not radar.empty:
            strongest = radar.iloc[0]
            best_upside = radar.sort_values(
                "상승여력(%)",
                ascending=False,
                na_position="last",
            ).iloc[0]

            st.success(
                f"최고 Alpha: **{strongest['종목명']} "
                f"{strongest['Alpha Score']:.1f}점**"
            )

            if pd.notna(best_upside["상승여력(%)"]):
                st.info(
                    f"최대 상승여력: **{best_upside['종목명']} "
                    f"{best_upside['상승여력(%)']:+.1f}%**"
                )

            st.write(
                f"컨센서스 보유 종목: **{consensus_count}개**"
            )
            st.write(
                f"30일 이상 미갱신: **{stale_count}개**"
            )

            if strong_count > 0:
                st.write(
                    f"Strong Buy/Buy 후보: **{strong_count}개**"
                )
            else:
                st.write(
                    "현재 Strong Buy/Buy 후보가 없습니다."
                )

        st.markdown("#### 시장 해석")

        if breadth["score"] is None:
            st.warning(
                "Breadth 데이터가 없습니다. "
                "Update Market Breadth를 실행하세요."
            )
        elif breadth["score"] >= 70:
            st.success(
                "상승 참여 종목이 넓은 강한 시장입니다. "
                "과열 여부를 함께 확인하세요."
            )
        elif breadth["score"] <= 30:
            st.warning(
                "시장 내부 확산이 약합니다. "
                "현금 비중과 분할 접근을 우선하세요."
            )
        else:
            st.info(
                "시장 내부가 혼조입니다. "
                "종목별 Alpha와 장기 Breadth를 함께 확인하세요."
            )

    st.divider()

    bottom_left, bottom_right = st.columns(2)

    with bottom_left:
        st.subheader("📰 최근 컨센서스 업데이트")

        if consensus.empty:
            st.info("저장된 컨센서스가 없습니다.")
        else:
            recent = (
                consensus.dropna(subset=["updated_at"])
                .sort_values("updated_at", ascending=False)
                .drop_duplicates(
                    subset=["ticker"],
                    keep="first",
                )
                .head(8)
            )

            if recent.empty:
                st.info(
                    "컨센서스 업데이트일이 입력되지 않았습니다."
                )
            else:
                recent_display = recent[
                    [
                        "name",
                        "year",
                        "operating_income_eok",
                        "target_por",
                        "updated_at",
                    ]
                ].rename(
                    columns={
                        "name": "종목",
                        "year": "연도",
                        "operating_income_eok": "예상 영업이익(억)",
                        "target_por": "목표 POR",
                        "updated_at": "업데이트일",
                    }
                )

                st.dataframe(
                    recent_display,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "예상 영업이익(억)": (
                            st.column_config.NumberColumn(
                                format="%,.1f억"
                            )
                        ),
                        "목표 POR": (
                            st.column_config.NumberColumn(
                                format="%.1f배"
                            )
                        ),
                        "업데이트일": (
                            st.column_config.DateColumn(
                                format="YYYY-MM-DD"
                            )
                        ),
                    },
                )

    with bottom_right:
        st.subheader("🎯 바로가기")

        if st.button(
            "⭐ Alpha Radar 열기",
            use_container_width=True,
            type="primary",
        ):
            try:
                st.switch_page(
                    "pages/05_Alpha_Radar.py"
                )
            except Exception:
                st.info(
                    "왼쪽 메뉴에서 Alpha Radar를 선택하세요."
                )

        if st.button(
            "📊 종목 분석 열기",
            use_container_width=True,
        ):
            st.session_state["main_page_selector"] = "📊 종목 분석"
            st.rerun()

        if st.button(
            "🌎 Market Breadth 열기",
            use_container_width=True,
        ):
            st.session_state["main_page_selector"] = "🌎 Market Breadth"
            st.rerun()

        st.caption(
            "HOME의 종목 카드에서 차트 보기를 누르면 "
            "해당 종목 분석 화면으로 바로 이동합니다."
        )
