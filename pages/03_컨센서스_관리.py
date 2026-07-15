import base64
import io
import json
import os
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st


st.set_page_config(
    page_title="컨센서스 관리",
    page_icon="📝",
    layout="wide",
)

DATA_DIR = Path("data")
CONSENSUS_XLSX = DATA_DIR / "consensus.xlsx"

GITHUB_OWNER = "firestory119-hub"
GITHUB_REPO = "POR-Hunting"
GITHUB_BRANCH = "main"
GITHUB_FILE_PATH = "data/consensus.xlsx"

YEAR_COLUMNS = [
    "2026E",
    "2027E",
    "2028E",
    "2029E",
    "2030E",
]

COLUMNS = [
    "종목명",
    "종목코드",
    *YEAR_COLUMNS,
    "목표POR",
    "출처",
    "업데이트일",
    "비고",
]


def clean_ticker(value) -> str:
    if value is None:
        return ""

    text = str(value).strip().replace(".0", "")
    digits = "".join(ch for ch in text if ch.isdigit())

    return digits.zfill(6) if digits else ""


def empty_consensus() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


@st.cache_data(show_spinner=False, ttl=60)
def load_consensus() -> pd.DataFrame:
    if not CONSENSUS_XLSX.exists():
        return empty_consensus()

    try:
        df = pd.read_excel(
            CONSENSUS_XLSX,
            sheet_name="컨센서스입력",
            header=1,
            dtype={"종목코드": str},
            engine="openpyxl",
        )
    except Exception:
        return empty_consensus()

    for column in COLUMNS:
        if column not in df.columns:
            df[column] = None

    df = df[COLUMNS].copy()
    df["종목코드"] = df["종목코드"].map(clean_ticker)

    for column in YEAR_COLUMNS + ["목표POR"]:
        df[column] = pd.to_numeric(
            df[column],
            errors="coerce",
        )

    df["업데이트일"] = pd.to_datetime(
        df["업데이트일"],
        errors="coerce",
    ).dt.date

    df = df[
        df["종목명"].notna()
        | df["종목코드"].astype(str).ne("")
    ].copy()

    return df.reset_index(drop=True)


def make_excel_bytes(df: pd.DataFrame) -> bytes:
    save_df = df.copy()

    for column in COLUMNS:
        if column not in save_df.columns:
            save_df[column] = None

    save_df = save_df[COLUMNS]
    save_df["종목코드"] = save_df["종목코드"].map(
        clean_ticker
    )

    save_df = save_df[
        save_df["종목명"].astype(str).str.strip().ne("")
        & save_df["종목코드"].astype(str).str.len().eq(6)
    ].copy()

    save_df = save_df.drop_duplicates(
        subset=["종목코드"],
        keep="last",
    )

    buffer = io.BytesIO()

    with pd.ExcelWriter(
        buffer,
        engine="openpyxl",
    ) as writer:
        title_df = pd.DataFrame(
            [["POR Hunting 연도별 영업이익 컨센서스"]],
        )
        title_df.to_excel(
            writer,
            sheet_name="컨센서스입력",
            index=False,
            header=False,
            startrow=0,
        )

        save_df.to_excel(
            writer,
            sheet_name="컨센서스입력",
            index=False,
            startrow=1,
        )

        guide_df = pd.DataFrame(
            [
                ["항목", "설명"],
                ["종목명", "앱의 종목명과 동일하게 입력"],
                ["종목코드", "6자리 종목코드"],
                ["2026E~2030E", "연도별 예상 영업이익(억원)"],
                ["목표POR", "종목별 목표 POR. 비우면 앱 기본값 사용"],
                ["출처", "증권사, 회사 가이던스, 사용자 추정 등"],
                ["업데이트일", "컨센서스를 수정한 날짜"],
                ["비고", "가정과 참고사항"],
            ]
        )
        guide_df.to_excel(
            writer,
            sheet_name="사용방법",
            index=False,
            header=False,
        )

        workbook = writer.book
        ws = workbook["컨센서스입력"]
        guide_ws = workbook["사용방법"]

        ws.merge_cells("A1:K1")
        ws["A1"] = "POR Hunting 연도별 영업이익 컨센서스"

        title_fill = "17365D"
        header_fill = "1F4E78"
        input_fill = "FFF2CC"

        from openpyxl.styles import Alignment, Font, PatternFill

        ws["A1"].fill = PatternFill(
            "solid",
            fgColor=title_fill,
        )
        ws["A1"].font = Font(
            bold=True,
            color="FFFFFF",
            size=15,
        )
        ws["A1"].alignment = Alignment(
            horizontal="center",
        )

        for cell in ws[2]:
            cell.fill = PatternFill(
                "solid",
                fgColor=header_fill,
            )
            cell.font = Font(
                bold=True,
                color="FFFFFF",
            )
            cell.alignment = Alignment(
                horizontal="center",
            )

        for row in ws.iter_rows(
            min_row=3,
            min_col=3,
            max_col=10,
        ):
            for cell in row:
                cell.fill = PatternFill(
                    "solid",
                    fgColor=input_fill,
                )

        widths = {
            "A": 15,
            "B": 12,
            "C": 12,
            "D": 12,
            "E": 12,
            "F": 12,
            "G": 12,
            "H": 10,
            "I": 24,
            "J": 14,
            "K": 34,
        }

        for column, width in widths.items():
            ws.column_dimensions[column].width = width

        ws.freeze_panes = "A3"

        for cell in ws["B"][2:]:
            cell.number_format = "@"

        for column in "CDEFG":
            for cell in ws[column][2:]:
                cell.number_format = "#,##0"

        for cell in ws["H"][2:]:
            cell.number_format = "0.0"

        for cell in ws["J"][2:]:
            cell.number_format = "yyyy-mm-dd"

        guide_ws.column_dimensions["A"].width = 20
        guide_ws.column_dimensions["B"].width = 70

    return buffer.getvalue()


def github_headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "POR-Hunting-Consensus-Manager",
        "Content-Type": "application/json",
    }


def get_github_sha(token: str) -> str | None:
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
        f"?ref={GITHUB_BRANCH}"
    )

    request = urllib.request.Request(
        url,
        method="GET",
        headers=github_headers(token),
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=20,
        ) as response:
            payload = json.loads(
                response.read().decode("utf-8")
            )
            return payload.get("sha")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None

        detail = exc.read().decode(
            "utf-8",
            errors="ignore",
        )[:300]
        raise RuntimeError(
            f"GitHub 파일 확인 실패({exc.code}): {detail}"
        ) from exc


def save_to_github(
    file_bytes: bytes,
    token: str,
) -> None:
    sha = get_github_sha(token)

    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/"
        f"{GITHUB_REPO}/contents/{GITHUB_FILE_PATH}"
    )

    payload = {
        "message": "Update consensus workbook",
        "content": base64.b64encode(
            file_bytes
        ).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }

    if sha:
        payload["sha"] = sha

    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="PUT",
        headers=github_headers(token),
    )

    try:
        with urllib.request.urlopen(
            request,
            timeout=40,
        ) as response:
            if response.status not in (200, 201):
                raise RuntimeError(
                    f"GitHub 저장 응답 코드: {response.status}"
                )
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(
            "utf-8",
            errors="ignore",
        )[:500]
        raise RuntimeError(
            f"GitHub 저장 실패({exc.code}): {detail}"
        ) from exc


st.title("📝 컨센서스 관리")
st.caption(
    "종목별 연도 예상 영업이익을 앱에서 수정하고 "
    "GitHub의 data/consensus.xlsx에 바로 저장합니다."
)

consensus_df = load_consensus()

c1, c2, c3 = st.columns(3)
c1.metric("등록 종목", f"{len(consensus_df):,}개")
c2.metric(
    "영업이익 입력 수",
    f"{int(consensus_df[YEAR_COLUMNS].notna().sum().sum()):,}개",
)
c3.metric(
    "최근 업데이트",
    (
        str(consensus_df["업데이트일"].dropna().max())
        if not consensus_df.empty
        and consensus_df["업데이트일"].notna().any()
        else "-"
    ),
)

st.info(
    "새 종목을 추가하려면 표의 맨 아래 빈 행에 입력하세요. "
    "종목코드는 반드시 6자리로 입력합니다."
)

edited_df = st.data_editor(
    consensus_df,
    num_rows="dynamic",
    use_container_width=True,
    hide_index=True,
    column_config={
        "종목명": st.column_config.TextColumn(
            "종목명",
            required=True,
        ),
        "종목코드": st.column_config.TextColumn(
            "종목코드",
            required=True,
            help="6자리 종목코드",
        ),
        "2026E": st.column_config.NumberColumn(
            "2026E 영업이익(억)",
            min_value=-100000.0,
            step=10.0,
            format="%.0f",
        ),
        "2027E": st.column_config.NumberColumn(
            "2027E 영업이익(억)",
            min_value=-100000.0,
            step=10.0,
            format="%.0f",
        ),
        "2028E": st.column_config.NumberColumn(
            "2028E 영업이익(억)",
            min_value=-100000.0,
            step=10.0,
            format="%.0f",
        ),
        "2029E": st.column_config.NumberColumn(
            "2029E 영업이익(억)",
            min_value=-100000.0,
            step=10.0,
            format="%.0f",
        ),
        "2030E": st.column_config.NumberColumn(
            "2030E 영업이익(억)",
            min_value=-100000.0,
            step=10.0,
            format="%.0f",
        ),
        "목표POR": st.column_config.NumberColumn(
            "목표 POR",
            min_value=0.0,
            max_value=100.0,
            step=0.5,
            format="%.1f",
        ),
        "출처": st.column_config.TextColumn(
            "출처",
        ),
        "업데이트일": st.column_config.DateColumn(
            "업데이트일",
            format="YYYY-MM-DD",
        ),
        "비고": st.column_config.TextColumn(
            "비고",
            width="large",
        ),
    },
    key="consensus_editor",
)

save_col, reload_col, download_col = st.columns(
    [1, 1, 2]
)

with save_col:
    save_clicked = st.button(
        "💾 GitHub에 저장",
        type="primary",
        use_container_width=True,
    )

with reload_col:
    if st.button(
        "🔄 다시 읽기",
        use_container_width=True,
    ):
        st.cache_data.clear()
        st.rerun()

excel_bytes = make_excel_bytes(edited_df)

with download_col:
    st.download_button(
        "📥 현재 컨센서스 엑셀 다운로드",
        data=excel_bytes,
        file_name="consensus.xlsx",
        mime=(
            "application/vnd.openxmlformats-officedocument."
            "spreadsheetml.sheet"
        ),
        use_container_width=True,
    )

if save_clicked:
    invalid_codes = edited_df[
        edited_df["종목명"].astype(str).str.strip().ne("")
        & edited_df["종목코드"].map(clean_ticker).str.len().ne(6)
    ]

    if not invalid_codes.empty:
        st.error(
            "종목코드가 6자리가 아닌 행이 있습니다."
        )
        st.stop()

    try:
        token = str(
            st.secrets["GITHUB_TOKEN"]
        ).strip()
    except Exception:
        st.error(
            "Streamlit Secrets에 GITHUB_TOKEN이 없습니다."
        )
        st.stop()

    if not token:
        st.error(
            "Streamlit Secrets의 GITHUB_TOKEN이 비어 있습니다."
        )
        st.stop()

    try:
        with st.spinner(
            "GitHub에 consensus.xlsx를 저장하는 중..."
        ):
            save_to_github(
                excel_bytes,
                token,
            )

        st.success(
            "저장 완료! 잠시 후 메인 app에서 "
            "새 컨센서스가 자동 반영됩니다."
        )
        st.cache_data.clear()

    except Exception as exc:
        st.error(str(exc))

with st.expander("사용 방법"):
    st.markdown(
        """
1. 종목별로 2026E~2030E 예상 영업이익을 입력합니다.
2. 종목별 목표 POR가 있으면 입력합니다.
3. **GitHub에 저장**을 누릅니다.
4. 메인 `app`에서 수동 예상 영업이익을 `0`으로 두면 저장된 컨센서스를 자동 사용합니다.
5. 메인 앱의 **저장된 연도별 영업이익 컨센서스** 표에서 전체 연도를 확인합니다.
        """
    )
