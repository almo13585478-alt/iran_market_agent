"""
RSS management page.
"""

import streamlit as st
from sqlalchemy import select

from database.session import get_session
from database.models import RssFeed
from news.rss_adapter import fetch_all_active, fetch_feed, add_feed, remove_feed


def render(adapter_choice: str):
    st.title("📡 RSS اخبار")
    st.caption(
        "فیدهای RSS منابع خبری. هنگام fetch، هر آیتم از طریق classifier "
        "طبقه‌بندی و با خبرهای موجود ادغام می‌شود."
    )

    s = get_session()
    try:
        tab_list, tab_add = st.tabs(["فیدها", "افزودن فید"])

        with tab_list:
            feeds = s.scalars(
                select(RssFeed).order_by(RssFeed.name)
            ).all()

            if not feeds:
                st.info("هیچ فیدی ثبت نشده.")
            else:
                st.subheader(f"{len(feeds)} فید")

                for f in feeds:
                    with st.container(border=True):
                        cc1, cc2, cc3 = st.columns([3, 1, 1])
                        cc1.markdown(f"**{f.name}**  \n`{f.url}`")
                        status = "🟢 سالم" if f.is_healthy else "⚪ نامشخص"
                        if f.last_error:
                            status = f"🔴 {f.last_error[:50]}"
                        cc2.caption(status)
                        if f.last_fetch_at:
                            cc3.caption(f"آخرین: {f.last_fetch_at.strftime('%m-%d %H:%M')}")

                        b1, b2, b3 = st.columns(3)
                        if b1.button("📥 fetch", key=f"fetch_{f.id}"):
                            with st.spinner(f"دریافت {f.name}..."):
                                r = fetch_feed(s, f)
                            if r["ok"]:
                                st.success(
                                    f"جدید: {r['new']} | رد: {r['skipped']}"
                                )
                            else:
                                st.error(r["error"])
                            st.rerun()
                        if b2.button(
                            "⏸ غیرفعال" if f.is_active else "▶️ فعال",
                            key=f"toggle_{f.id}",
                        ):
                            f.is_active = not f.is_active
                            s.commit()
                            st.rerun()
                        if b3.button("🗑 حذف", key=f"rm_{f.id}"):
                            remove_feed(s, f.id)
                            st.rerun()

                st.divider()
                if st.button("📥 fetch همه فیدهای فعال"):
                    with st.spinner("دریافت همه..."):
                        results = fetch_all_active(s)
                    total_new = sum(r["new"] for r in results)
                    total_fail = sum(1 for r in results if not r["ok"])
                    st.success(
                        f"{len(results)} فید — {total_new} خبر جدید، "
                        f"{total_fail} خطا"
                    )
                    for r in results:
                        if not r["ok"]:
                            st.caption(f"❌ {r['feed']}: {r['error']}")

        with tab_add:
            with st.form("add_feed", clear_on_submit=True):
                name = st.text_input("نام فید").strip()
                url = st.text_input("URL فید (باید RSS/Atom باشد)").strip()
                submitted = st.form_submit_button("افزودن")
            if submitted:
                ok, msg = add_feed(s, name, url)
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()

            st.divider()
            st.markdown("**نمونه فیدهای پیشنهادی:**")
            st.code(
                "نام: donya\n"
                "URL: https://donya-e-eqtesad.com/rss\n\n"
                "نام: sena\n"
                "URL: https://www.sena.ir/rss.aspx\n\n"
                "نام: bourse24\n"
                "URL: https://www.bourse24.ir/rss\n\n"
                "نام: tahlilbazaar\n"
                "URL: https://www.tahlilbazaar.com/feed",
                language="text",
            )
            st.caption(
                "⚠️ برخی فیدها ممکن است تغییر کرده یا دسترسی محدود داشته باشند. "
                "اگر fetch شکست خورد، URL را تغییر دهید یا حذف کنید."
            )
    finally:
        s.close()