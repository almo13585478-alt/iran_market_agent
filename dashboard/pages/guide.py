"""Guide page: 5-step workflow, glossary, FAQ."""

import streamlit as st

from dashboard.help_content import (
    FAQ, GLOSSARY, WORKFLOW_STEPS,
)


def render(adapter_choice: str) -> None:
    st.title("📖 راهنمای کامل")
    st.caption(
        "همه چیز در یک صفحه: شروع سریع، واژه‌نامه، سوالات رایج."
    )

    tab_start, tab_glossary, tab_faq = st.tabs([
        "🚀 شروع سریع", "📚 واژه‌نامه", "❓ سوالات رایج",
    ])

    with tab_start:
        _render_quick_start()

    with tab_glossary:
        _render_glossary()

    with tab_faq:
        _render_faq()


def _render_quick_start() -> None:
    st.markdown("### 🚀 ۵ گام استفاده روزانه")
    st.caption(
        "این‌ها را به ترتیب دنبال کنید. اگر دفعه اول است، **۲-۳ روز** "
        "فقط تماشا کنید قبل از اینکه خودتان تصمیم بگیرید."
    )

    for step in WORKFLOW_STEPS:
        with st.container(border=True):
            c1, c2 = st.columns([1, 10])
            c1.markdown(
                f"<div style='background:linear-gradient(135deg, #008e73, "
                f"#00d4aa);color:#fff;width:44px;height:44px;"
                f"border-radius:50%;display:flex;align-items:center;"
                f"justify-content:center;font-size:20px;font-weight:700;'>"
                f"{step['n']}</div>",
                unsafe_allow_html=True,
            )
            with c2:
                st.markdown(f"#### {step['title']}")
                st.caption(f"**چرا؟** {step['why']}")
                st.markdown("**چه کار کنید:**")
                for item in step["what_to_do"]:
                    st.markdown(f"- {item}")
                st.info(f"💡 {step['tip']}")

    st.divider()
    st.markdown("### 📅 چک‌لیست روزانه")
    checklist = [
        "صفحه «⚡ امروز» را باز کنید",
        "گام ۱: داده سبز است؟",
        "گام ۲: وضعیت بازار را بخوانید",
        "گام ۳: سیگنال‌ها را بررسی کنید",
        "گام ۴: اقدام کنید (یا نکنید)",
        "پایان روز: Paper/Portfolio را ثبت کنید",
    ]
    for item in checklist:
        st.markdown(f"- [ ] {item}")

    st.divider()
    st.markdown("### ⏰ چه زمانی چه کاری")
    schedule = [
        ("🌅 صبح (۹:۰۰)", "صفحه ⚡ امروز → بررسی داده، بازار، سیگنال‌ها"),
        ("🌆 بعد از بازار (۱۸:۰۰)", "سینک قیمت + Paper scan"),
        ("🌙 شب (۲۱:۰۰)", "بررسی Paper + نگاه به بریفینگ فردا"),
        ("📅 جمعه (۱۹:۰۰)", "گزارش هفتگی + WF خودکار"),
        ("🛠️ ماهی یک‌بار", "سیستم → پشتیبان + بررسی سلامت"),
    ]
    for time, task in schedule:
        st.markdown(f"- **{time}** → {task}")


def _render_glossary() -> None:
    st.markdown("### 📚 واژه‌نامه")
    st.caption(
        "همه‌ی اصطلاحات فنی. روی هر کدام کلیک کنید تا توضیح کامل ببینید."
    )

    for term, info in GLOSSARY.items():
        with st.expander(f"**{term}** — {info['full']}"):
            st.markdown(info["meaning"])


def _render_faq() -> None:
    st.markdown("### ❓ سوالات رایج")
    st.caption(
        "اگر سوال شما اینجا نبود، از صفحه «🤖 دستیار» بپرسید."
    )

    for i, item in enumerate(FAQ, 1):
        with st.expander(f"**{i}. {item['q']}**"):
            st.markdown(item["a"])


def render_sidebar_help() -> None:
    """Compact help box for sidebar."""
    with st.sidebar.expander("❓ راهنمای سریع"):
        st.markdown(
            """
            **شروع سریع:**
            1. **⚡ امروز** — همه چیز از این‌جا
            2. **🎯 سیگنال** — بعد از رتبه‌بندی
            3. **📝 Paper** — برای تست
            4. **🛠️ سیستم** — وقتی آماده پول واقعی

            **قوانین طلایی:**
            - ✅ بازار قرمز؟ حجم نصف.
            - ✅ خبر منفی؟ صبر کنید.
            - ✅ ۰ سیگنال؟ طبیعی است.
            - ❌ بدون ۲۰+ Paper وارد نشوید.
            - ❌ حد ضرر جابجا نکنید.

            **برای جزئیات بیشتر:** صفحه 📖 راهنما
            """
        )