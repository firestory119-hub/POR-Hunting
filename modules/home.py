import streamlit as st


def show_home():
    st.title("🏠 POR Alpha")

    st.caption("Value + Market Breadth + AI Dashboard")

    col1, col2, col3 = st.columns(3)

    with col1:
        st.metric("📈 KOSPI", "-")

    with col2:
        st.metric("🌎 Breadth", "-")

    with col3:
        st.metric("⭐ Alpha Score", "준비중")

    st.divider()

    st.subheader("🤖 AI Market")

    st.info(
        """
현재 시장 상태를 분석합니다.

✔ Breadth

✔ POR

✔ VIX

✔ Sector

데이터를 종합하여 AI 의견을 제공합니다.
"""
    )

    st.divider()

    st.subheader("🚀 Coming Soon")

    st.write("• Market Dashboard")

    st.write("• Sector Dashboard")

    st.write("• POR Market")

    st.write("• AI Report")
