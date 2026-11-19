"""Weekly report page."""

from datetime import datetime

import streamlit as st

from database.session import get_session
from reports.weekly_report import build_weekly_report, to_markdown, save_weekly_report


def render(adapter_choice: str):
    st.title("📅 گزارش هفتگی")
    st.caption("خلاصه ۷ روز اخیر: Paper، بازار، پرتفوی، اخبار.")

    s = get_session()
    try:
        days = st.number_input("تعداد روز", 3, 30, 7, 1, key="w_days")
        c1, c2 = st.columns(2)
        if c1.button("📄 تولید گزارش"):
            st.session_state["weekly"] = build_weekly_report(s, days=int(days))
        if c2.button("💾 ذخیره در فایل"):
            if "weekly" not in st.session_state:
                st.session_state["weekly"] = build_weekly_report(s, days=int(days))
            path = save_weekly_report(st.session_state["weekly"])
            st.success(f"ذخیره شد: `{path}`")

        rpt = st.session_state.get("weekly")
        if not rpt:
            st.info("دکمه «تولید گزارش» را بزنید.")
            return

        md = to_markdown(rpt)
        st.download_button(
            "⬇️ دانلود Markdown",
            data=md.encode("utf-8"),
            file_name=f"weekly_{rpt.week_end.strftime('%Y-%m-%d')}.md",
            mime="text/markdown",
        )
        st.markdown(md)
    finally:
        s.close()