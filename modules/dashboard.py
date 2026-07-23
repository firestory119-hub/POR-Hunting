import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _load_breadth(path: str) -> pd.DataFrame:
    if not os.path.exists(path):
        return pd.DataFrame()

    try:
        df = pd.read_csv(path)
    except Exception:
        return pd.DataFrame()

    if "date" not in df.columns or "market" not in df.columns:
        return pd.DataFrame()

    df["date"] = pd.to_datetime(df["date"], errors="coerce")

    numeric_cols = [
        "above_ma20",
        "above_ma60",
        "above_ma120",
        "above_ma200",
        "advancers",
        "decliners",
        "unchanged",
        "ad_net",
        "ad_line",
        "new_high_52w",
        "new_low_52w",
        "index_close",
        "vkospi",
    ]
    for col in numeric_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    return (
        df.dropna(subset=["date"])
        .sort_values(["market", "date"])
        .reset_index(drop=True)
    )


def _score(row: pd.Series) -> float:
    weights = {
        "above_ma20": 0.35,
        "above_ma60": 0.25,
        "above_ma120": 0.20,
        "above_ma200": 0.20,
    }

    total = 0.0
    used = 0.0

    for col, weight in weights.items():
        value = pd.to_numeric(row.get(col), errors="coerce")
        if pd.notna(value):
            total += max(0.0, min(100.0, float(value))) * weight
            used += weight

    return round(total / used, 1) if used else 50.0


def _signal(row: pd.Series) -> tuple[str, str]:
    score = _score(row)
    ad_net = pd.to_numeric(row.get("ad_net"), errors="coerce")
    ma200 = pd.to_numeric(row.get("above_ma200"), errors="coerce")

    ad_net = float(ad_net) if pd.notna(ad_net) else 0.0
    ma200 = float(ma200) if pd.notna(ma200) else 50.0

    if score >= 70 and ad_net >= 0 and ma200 >= 55:
        return "공격", "상승 참여 종목이 넓고 장기 추세도 강합니다."
    if score <= 30 and ad_net < 0:
        return "방어", "시장 내부가 약하고 하락 종목이 우세합니다."
    if score <= 40 and ma200 <= 25:
        return "분할관찰", "장기 Breadth가 낮아 바닥 탐색 가능성을 확인할 구간입니다."
    return "중립", "지수보다 60일·200일 Breadth 개선 여부를 확인하세요."


def render_market_dashboard(breadth_csv: str) -> None:
    st.title("📊 POR Alpha Market Dashboard")
    st.caption("시장 방향·확산도·수급 강도를 한 화면에서 확인합니다.")

    df = _load_breadth(breadth_csv)
    if df.empty:
        st.warning("Breadth 데이터가 없습니다.")
        st.code("Actions → Update Market Breadth → Run workflow", language="text")
        return

    market = st.radio(
        "시장",
        ["KOSPI", "KOSDAQ"],
        horizontal=True,
        key="dashboard_market",
    )

    mdf = df[df["market"] == market].copy()
    if mdf.empty:
        st.warning(f"{market} 데이터가 없습니다.")
        return

    latest = mdf.iloc[-1]
    previous = mdf.iloc[-2] if len(mdf) > 1 else latest
    score = _score(latest)
    signal, signal_text = _signal(latest)

    ma20 = float(pd.to_numeric(latest.get("above_ma20"), errors="coerce"))
    ma60 = float(pd.to_numeric(latest.get("above_ma60"), errors="coerce"))
    ma200 = float(pd.to_numeric(latest.get("above_ma200"), errors="coerce"))
    ad_net = float(pd.to_numeric(latest.get("ad_net"), errors="coerce") or 0)
    index_close = pd.to_numeric(latest.get("index_close"), errors="coerce")
    vkospi = pd.to_numeric(latest.get("vkospi"), errors="coerce")

    prev_index = pd.to_numeric(previous.get("index_close"), errors="coerce")
    index_delta = (
        float(index_close - prev_index)
        if pd.notna(index_close) and pd.notna(prev_index)
        else None
    )

    r1 = st.columns(4)
    r1[0].metric(
        f"{market} 지수",
        f"{index_close:,.2f}" if pd.notna(index_close) else "-",
        f"{index_delta:+,.2f}" if index_delta is not None else None,
    )
    r1[1].metric("20일선 위", f"{ma20:.1f}%")
    r1[2].metric("200일선 위", f"{ma200:.1f}%")
    r1[3].metric("Alpha Market Score", f"{score:.1f}/100")

    r2 = st.columns(4)
    r2[0].metric("60일선 위", f"{ma60:.1f}%")
    r2[1].metric("상승-하락", f"{ad_net:,.0f}")
    r2[2].metric("VKOSPI", f"{vkospi:.2f}" if pd.notna(vkospi) else "수집 전")
    r2[3].metric("시장 신호", signal)

    st.progress(int(max(0, min(100, score))))
    st.info(f"**현재 판단: {signal}** — {signal_text}")

    chart_df = mdf.tail(260).copy()
    if "index_close" in chart_df.columns:
        chart_df["index_ma200"] = (
            chart_df["index_close"]
            .rolling(200, min_periods=20)
            .mean()
        )

    fig = go.Figure()

    if "above_ma200" in chart_df.columns:
        fig.add_trace(
            go.Scatter(
                x=chart_df["date"],
                y=chart_df["above_ma200"],
                name="200일선 위 종목 비율",
                mode="lines",
                fill="tozeroy",
                opacity=0.35,
                yaxis="y",
                hovertemplate="%{x|%Y-%m-%d}<br>200일선 위 %{y:.1f}%<extra></extra>",
            )
        )

    if "index_close" in chart_df.columns:
        fig.add_trace(
            go.Scatter(
                x=chart_df["date"],
                y=chart_df["index_close"],
                name=market,
                mode="lines",
                yaxis="y2",
                hovertemplate=f"%{{x|%Y-%m-%d}}<br>{market} %{{y:,.2f}}<extra></extra>",
            )
        )

        fig.add_trace(
            go.Scatter(
                x=chart_df["date"],
                y=chart_df["index_ma200"],
                name="지수 200일선",
                mode="lines",
                line=dict(dash="dash"),
                yaxis="y2",
                hovertemplate="%{x|%Y-%m-%d}<br>200일선 %{y:,.2f}<extra></extra>",
            )
        )

    if "vkospi" in chart_df.columns and chart_df["vkospi"].notna().any():
        fig.add_trace(
            go.Scatter(
                x=chart_df["date"],
                y=chart_df["vkospi"],
                name="VKOSPI",
                mode="lines",
                line=dict(dash="dot"),
                yaxis="y3",
                hovertemplate="%{x|%Y-%m-%d}<br>VKOSPI %{y:.2f}<extra></extra>",
            )
        )

    fig.add_hline(y=20, line_dash="dot", annotation_text="침체 20%", yref="y")
    fig.add_hline(y=80, line_dash="dot", annotation_text="과열 80%", yref="y")

    fig.update_layout(
        title=f"{market} · 200일선 · 장기 Breadth",
        height=620,
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08, x=0),
        margin=dict(l=45, r=80, t=90, b=45),
        yaxis=dict(
            title="200일선 위 종목 비율(%)",
            range=[0, 100],
            side="left",
        ),
        yaxis2=dict(
            title=market,
            overlaying="y",
            side="right",
            showgrid=False,
        ),
        yaxis3=dict(
            title="VKOSPI",
            overlaying="y",
            side="right",
            position=0.94,
            showgrid=False,
        ),
    )
    st.plotly_chart(fig, use_container_width=True)

    st.subheader("이동평균선별 확산도")
    spread_cols = st.columns(4)
    spread = [
        ("20일선", ma20),
        ("60일선", ma60),
        (
            "120일선",
            float(pd.to_numeric(latest.get("above_ma120"), errors="coerce")),
        ),
        ("200일선", ma200),
    ]

    for box, (label, value) in zip(spread_cols, spread):
        box.markdown(f"**{label}**")
        box.progress(int(max(0, min(100, value))))
        state = "과열권" if value >= 80 else "침체권" if value <= 20 else "중립권"
        box.caption(f"{value:.1f}% · {state}")
