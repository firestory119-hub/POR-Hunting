import os
import re

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="컨센서스 관리",
    page_icon="🧾",
    layout="wide",
)

DATA_DIR = "data"
CONSENSUS_XLSX = os.path.join(DATA_DIR, "consensus.xlsx")


def clean_ticker(value) -> str:
    text = str(value or "").strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())
    return digits.zfill(6) if digits else ""


@st.cache_data(show_spinner=False, ttl=300)
def load_consensus_wide() -> pd.DataFrame:
    if not os.path.exists(CONSENSUS_XLSX):
        return pd.DataFrame()

    try:
        df = pd.read_excel(
            CONSENSUS_XLSX,
            sheet_name="컨센서스입력",
            header=1,
            dtype={"종목코드": str},
            engine="openpyxl",
        )
    except Exception:
        return pd.DataFrame()

    if "종목코드" in df.columns:
        df["종목코드"] = df["종목코드"].map(clean_ticker)

    if "업데이트일" in df.columns:
        df["업데이트일"] = pd.to_datetime(
            df["업데이트일"],
            errors="coerce",
        )

    for column in df.columns:
        if re.fullmatch(r"\d{4}E?", str(column).strip()):
            df[column] = pd.to_numeric(
                df[column],
                errors="coerce",
            )

    if "목표POR" in df.columns:
        df["목표POR"] = pd.to_numeric(
            df["목표POR"],
            errors="coerce",
        )

    return df


def apply_sort(
    df: pd.DataFrame,
    sort_option: str,
    selected_year: str | None,
) -> pd.DataFrame:
    if df.empty:
        return df

    result = df.copy()

    if sort_option == "종목명 가나다순":
        if "종목명" in result.columns:
            result = result.sort_values(
                "종목명",
                ascending=True,
                kind="stable",
                na_position="last",
            )

    elif sort_option == "종목명 역순":
        if "종목명" in result.columns:
            result = result.sort_values(
                "종목명",
                ascending=False,
                kind="stable",
                na_position="last",
            )

    elif sort_option == "최근 업데이트순":
        if "업데이트일" in result.columns:
            result = result.sort_values(
                "업데이트일",
                ascending=False,
                kind="stable",
                na_position="last",
            )

    elif sort_option == "오래된 업데이트순":
        if "업데이트일" in result.columns:
            result = result.sort_values(
                "업데이트일",
                ascending=True,
                kind="stable",
                na_position="last",
            )

    elif sort_option == "목표 POR 높은순":
        if "목표POR" in result.columns:
            result = result.sort_values(
                "목표POR",
                ascending=False,
                kind="stable",
                na_position="last",
            )

    elif sort_option == "목표 POR 낮은순":
        if "목표POR" in result.columns:
            result = result.sort_values(
                "목표POR",
                ascending=True,
                kind="stable",
                na_position="last",
            )

    elif (
        sort_option == "예상 영업이익 높은순"
        and selected_year in result.columns
    ):
        result = result.sort_values(
            selected_year,
            ascending=False,
            kind="stable",
            na_position="last",
        )

    elif (
        sort_option == "예상 영업이익 낮은순"
        and selected_year in result.columns
    ):
        result = result.sort_values(
            selected_year,
            ascending=True,
            kind="stable",
            na_position="last",
        )

    return result.reset_index(drop=True)


st.title("🧾 컨센서스 관리")
st.caption(
    "종목명 검색과 가나다순 정렬로 저장된 컨센서스를 빠르게 확인합니다."
)

consensus = load_consensus_wide()

if consensus.empty:
    st.warning(
        "data/consensus.xlsx의 '컨센서스입력' 시트를 읽지 못했습니다."
    )
    st.stop()

year_columns = [
    str(column)
    for column in consensus.columns
    if re.fullmatch(r"\d{4}E?", str(column).strip())
]

top1, top2, top3 = st.columns([2, 1.5, 1.5])

with top1:
    search_text = st.text_input(
        "종목명 또는 종목코드 검색",
        placeholder="예: 심텍, 222800",
    ).strip()

with top2:
    sort_option = st.selectbox(
        "정렬 기준",
        [
            "종목명 가나다순",
            "종목명 역순",
            "최근 업데이트순",
            "오래된 업데이트순",
            "목표 POR 높은순",
            "목표 POR 낮은순",
            "예상 영업이익 높은순",
            "예상 영업이익 낮은순",
        ],
        index=0,
    )

with top3:
    selected_year = st.selectbox(
        "영업이익 정렬 연도",
        year_columns if year_columns else ["없음"],
        index=0,
        disabled=not bool(year_columns),
    )

filtered = consensus.copy()

if search_text:
    name_mask = (
        filtered.get("종목명", pd.Series("", index=filtered.index))
        .astype(str)
        .str.contains(search_text, case=False, na=False)
    )
    ticker_mask = (
        filtered.get("종목코드", pd.Series("", index=filtered.index))
        .astype(str)
        .str.contains(search_text, case=False, na=False)
    )
    filtered = filtered[name_mask | ticker_mask].copy()

filtered = apply_sort(
    filtered,
    sort_option,
    selected_year if year_columns else None,
)

m1, m2, m3 = st.columns(3)
m1.metric("전체 종목", f"{len(consensus):,}개")
m2.metric("검색 결과", f"{len(filtered):,}개")

if "업데이트일" in filtered.columns:
    latest_update = filtered["업데이트일"].max()
    m3.metric(
        "최근 업데이트",
        (
            latest_update.strftime("%Y-%m-%d")
            if pd.notna(latest_update)
            else "-"
        ),
    )
else:
    m3.metric("최근 업데이트", "-")

display_columns = [
    column
    for column in [
        "종목명",
        "종목코드",
        *year_columns,
        "목표POR",
        "출처",
        "업데이트일",
        "비고",
    ]
    if column in filtered.columns
]

column_config = {}

for year in year_columns:
    column_config[year] = st.column_config.NumberColumn(
        year,
        format="%,.1f억",
    )

if "목표POR" in display_columns:
    column_config["목표POR"] = st.column_config.NumberColumn(
        "목표 POR",
        format="%.1f배",
    )

if "업데이트일" in display_columns:
    column_config["업데이트일"] = st.column_config.DateColumn(
        "업데이트일",
        format="YYYY-MM-DD",
    )

st.dataframe(
    filtered[display_columns],
    use_container_width=True,
    hide_index=True,
    column_config=column_config,
)

st.caption(
    "기본 정렬은 종목명 가나다순입니다. "
    "표의 열 제목을 클릭해서도 임시 정렬할 수 있습니다."
)

csv_data = filtered[display_columns].to_csv(
    index=False,
    encoding="utf-8-sig",
)

st.download_button(
    "📥 현재 정렬 결과 CSV 다운로드",
    data=csv_data,
    file_name="consensus_sorted.csv",
    mime="text/csv",
)

if st.button("🔄 컨센서스 다시 읽기"):
    load_consensus_wide.clear()
    st.rerun()
