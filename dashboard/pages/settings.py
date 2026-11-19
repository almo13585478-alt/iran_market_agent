"""Settings page: persisted parameters, reset, load/save."""

import streamlit as st
from sqlalchemy import select

from database.session import get_session
from settings.manager import (
    SETTINGS_DEFAULTS, SETTINGS_LABELS,
    cast_value, get_all, reset_to_defaults, save_all,
)


def render(adapter_choice: str) -> None:
    st.title("⚙️ تنظیمات")
    st.caption(
        "همه‌ی پارامترها یک‌جا. با «💾 ذخیره» در دیتابیس می‌مانند و "
        "بار بعد که مرورگر را باز کنید، خودکار بازیابی می‌شوند."
    )

    # Load current settings from DB
    s = get_session()
    try:
        current = get_all(s)
    finally:
        s.close()

    tab_trading, tab_paper, tab_ui, tab_reset = st.tabs([
        "💰 معامله", "📝 Paper", "🎨 ظاهر", "♻️ بازیابی",
    ])

    # ---------- Trading tab ----------
    with tab_trading:
        st.subheader("تنظیمات اصلی معامله")
        st.caption(
            "این‌ها بر صفحه «⚡ امروز»، «🎯 سیگنال» و همه محاسبات "
            "ریسک اثر می‌گذارند."
        )

        c1, c2 = st.columns(2)
        with c1:
            pv = st.number_input(
                "💰 ارزش سبد (ریال)",
                min_value=1_000_000,
                max_value=10_000_000_000_000,
                value=int(current.get("portfolio_value", 1_000_000_000)),
                step=100_000_000,
                format="%d",
                key="set_portfolio_value",
                help=(
                    "کل سرمایه‌ای که در این استراتژی می‌گذارید. "
                    "حجم پیشنهادی هر معامله بر این اساس محاسبه می‌شود."
                ),
            )
        with c2:
            rp = st.number_input(
                "📊 ریسک هر معامله (٪)",
                min_value=0.1,
                max_value=5.0,
                value=float(current.get("risk_pct", 0.5)),
                step=0.1,
                key="set_risk_pct",
                help=(
                    "چند درصد از سبد در هر معامله ریسک می‌کنید؟ "
                    "۰.۵٪ استاندارد است."
                ),
            )

        c3, c4 = st.columns(2)
        with c3:
            mr = st.number_input(
                "⚖️ حداقل R/R",
                min_value=1.0,
                max_value=5.0,
                value=float(current.get("min_rr", 2.0)),
                step=0.5,
                key="set_min_rr",
                help="نسبت حداقل سود به ریسک. زیر ۲ ارزش معامله ندارد.",
            )
        with c4:
            dss = st.number_input(
                "🔢 تعداد نماد پیش‌فرض تحلیل",
                min_value=10,
                max_value=200,
                value=int(current.get("default_scan_symbols", 30)),
                step=10,
                key="set_default_scan_symbols",
                help="صفحه «⚡ امروز» چند نماد را سریع تحلیل کند.",
            )

        if st.button(
            "💾 ذخیره تنظیمات معامله",
            key="save_trading",
            type="primary",
            use_container_width=True,
        ):
            s2 = get_session()
            try:
                results = save_all(s2, {
                    "portfolio_value": int(pv),
                    "risk_pct": float(rp),
                    "min_rr": float(mr),
                    "default_scan_symbols": int(dss),
                })
                # Update session_state cache for immediate use
                st.session_state["_setting_portfolio_value"] = int(pv)
                st.session_state["_setting_risk_pct"] = float(rp)
                st.session_state["_setting_min_rr"] = float(mr)
                st.session_state["_setting_default_scan_symbols"] = int(dss)
                if all(results.values()):
                    st.success("✅ ذخیره شد — بار بعد خودکار بازیابی می‌شود.")
                else:
                    st.warning(f"⚠️ بخشی ذخیره نشد: {results}")
            finally:
                s2.close()

    # ---------- Paper tab ----------
    with tab_paper:
        st.subheader("پیش‌فرض‌های Paper Trading")
        st.caption(
            "این مقادیر در صفحه «📝 Paper Trading» به‌عنوان مقدار اولیه "
            "اسکن نمایش داده می‌شوند."
        )

        c1, c2, c3 = st.columns(3)
        with c1:
            pms = st.number_input(
                "📝 حداقل فیلتر",
                min_value=3, max_value=6,
                value=int(current.get("paper_min_soft", 5)),
                step=1, key="set_paper_min_soft",
                help="۶ فیلتر soft v2b — حداقل چندتا پاس شوند؟",
            )
        with c2:
            pam = st.number_input(
                "📝 ATR×",
                min_value=1.0, max_value=4.0,
                value=float(current.get("paper_atr_mult", 2.0)),
                step=0.5, key="set_paper_atr_mult",
                help="حد ضرر = ATR × این ضریب",
            )
        with c3:
            prr = st.number_input(
                "📝 R/R",
                min_value=1.0, max_value=4.0,
                value=float(current.get("paper_rr_ratio", 2.0)),
                step=0.5, key="set_paper_rr_ratio",
                help="هدف = R × این ضریب",
            )

        if st.button(
            "💾 ذخیره تنظیمات Paper",
            key="save_paper",
            type="primary",
            use_container_width=True,
        ):
            s2 = get_session()
            try:
                results = save_all(s2, {
                    "paper_min_soft": int(pms),
                    "paper_atr_mult": float(pam),
                    "paper_rr_ratio": float(prr),
                })
                if all(results.values()):
                    st.success("✅ ذخیره شد")
                else:
                    st.warning(f"⚠️ بخشی ذخیره نشد")
            finally:
                s2.close()

    # ---------- UI tab ----------
    with tab_ui:
        st.subheader("ظاهر")
        theme = st.radio(
            "🎨 تم",
            ["روشن", "تیره"],
            index=0 if current.get("theme", "روشن") == "روشن" else 1,
            horizontal=True,
            key="set_theme",
        )

        if st.button(
            "💾 ذخیره ظاهر",
            key="save_ui",
            type="primary",
            use_container_width=True,
        ):
            s2 = get_session()
            try:
                ok = save_all(s2, {"theme": theme})
                st.session_state["theme_toggle"] = theme
                if all(ok.values()):
                    st.success("✅ ذخیره شد — با refresh اعمال می‌شود.")
                    st.rerun()
            finally:
                s2.close()

    # ---------- Reset tab ----------
    with tab_reset:
        st.subheader("♻️ بازیابی به پیش‌فرض")
        st.warning(
            "⚠️ همه‌ی تنظیمات به مقادیر پیش‌فرض برمی‌گردند. "
            "داده‌های دیگر (پرتفوی، Paper، بنیادی، اخبار) **دست‌نخورده** می‌مانند."
        )
        st.markdown("**مقادیر پیش‌فرض:**")
        rows = []
        for k, (label, _) in SETTINGS_LABELS.items():
            rows.append({
                "پارامتر": label,
                "مقدار پیش‌فرض": str(SETTINGS_DEFAULTS.get(k, "-")),
                "مقدار فعلی": str(current.get(k, "-")),
            })
        st.dataframe(rows, width="stretch", hide_index=True)

        confirm = st.checkbox(
            "متوجه‌ام و می‌خواهم به پیش‌فرض برگردم",
            key="reset_confirm",
        )
        if st.button(
            "♻️ بازیابی به پیش‌فرض",
            type="secondary",
            disabled=not confirm,
            use_container_width=True,
        ):
            s2 = get_session()
            try:
                reset_to_defaults(s2)
                # Update session_state cache
                for k, v in SETTINGS_DEFAULTS.items():
                    st.session_state[f"_setting_{k}"] = v
                st.success("✅ همه تنظیمات به پیش‌فرض برگشتند.")
                st.rerun()
            finally:
                s2.close()

    # ---------- Summary footer ----------
    st.divider()
    st.caption(
        "💡 **نکته:** هر تغییری که در سایدبار (ارزش سبد، ریسک، R/R) می‌دهید، "
        "موقت است — برای دائمی شدن، «💾 ذخیره تنظیمات معامله» را بزنید "
        "یا از دکمه‌ی «💾 ذخیره» در سایدبار استفاده کنید."
    )