"""System page: health, backup, real-money readiness."""

import json
from pathlib import Path

import streamlit as st

from database.session import get_session
from scripts.backup_db import (
    cleanup_old_backups, delete_backup, export_json,
    full_db_backup, list_backups,
)
from scripts.system_health import compute_health, HealthItem, ReadinessItem


STATUS_ICON = {
    "ok": "✅", "warn": "⚠️", "fail": "🔴", "unknown": "⚪",
    "pass": "✅", "unknown_r": "⚪",
}


def _icon(status: str) -> str:
    return {
        "ok": "✅", "warn": "⚠️", "fail": "🔴",
        "pass": "✅",
    }.get(status, "⚪")


def _color(status: str) -> str:
    return {
        "ok": "#26a69a", "pass": "#26a69a",
        "warn": "#ffc107", "fail": "#ef5350",
    }.get(status, "#888")


def render(adapter_choice: str):
    st.title("🛠️ سیستم")
    st.caption(
        "سلامت، پشتیبان‌گیری، و چک‌لیست آمادگی برای پول واقعی."
    )

    tab_health, tab_backup, tab_readiness = st.tabs([
        "🩺 سلامت", "💾 پشتیبان", "🎯 آمادگی پول واقعی",
    ])

    with tab_health:
        _render_health()

    with tab_backup:
        _render_backup()

    with tab_readiness:
        _render_readiness()


def _render_health():
    s = get_session()
    try:
        rpt = compute_health(s)
    finally:
        s.close()

    # Overall status
    overall_color = _color(rpt.overall_status)
    overall_label = {
        "ok": "همه چیز سالم است",
        "warn": "هشدارهایی وجود دارد",
        "fail": "مشکلات جدی — نیاز به اقدام",
    }.get(rpt.overall_status, "نامشخص")

    st.markdown(
        f"<div style='background:rgba(255,255,255,0.03);"
        f"border-right:4px solid {overall_color};"
        f"padding:12px 16px;border-radius:6px;margin:8px 0;'>"
        f"<b>{_icon(rpt.overall_status)} {overall_label}</b> — "
        f"<span style='color:#888;font-size:12px;'>"
        f"{rpt.computed_at.strftime('%Y-%m-%d %H:%M')}</span></div>",
        unsafe_allow_html=True,
    )

    # Table
    rows = []
    for item in rpt.items:
        rows.append({
            "وضعیت": _icon(item.status),
            "مورد": item.name,
            "مقدار": item.value,
            "جزئیات": item.detail or "-",
            "اقدام": item.action or "-",
        })

    st.dataframe(rows, width="stretch", hide_index=True)

    # Summary counts
    counts = {"ok": 0, "warn": 0, "fail": 0}
    for item in rpt.items:
        if item.status in counts:
            counts[item.status] += 1

    c1, c2, c3 = st.columns(3)
    c1.metric("سالم", counts["ok"])
    c2.metric("هشدار", counts["warn"])
    c3.metric("خطا", counts["fail"])

    if st.button("🔄 بازخوانی سلامت"):
        st.rerun()


def _render_backup():
    st.subheader("💾 پشتیبان‌گیری")
    st.caption(
        "دو نوع پشتیبان: **کامل (db)** برای بازیابی سریع، "
        "**JSON** برای بازرسی و انتقال."
    )

    c1, c2 = st.columns(2)

    with c1:
        st.markdown("### 📦 پشتیبان کامل")
        st.caption("کپی کامل فایل دیتابیس. برای بازیابی، فایل را برگردانید.")
        if st.button("💾 پشتیبان کامل بگیر", key="backup_db",
                     use_container_width=True):
            with st.spinner("در حال کپی..."):
                ok, msg = full_db_backup()
            (st.success if ok else st.error)(msg)

    with c2:
        st.markdown("### 📋 خروجی JSON")
        st.caption("خروجی متنی از جداول داده کاربر (پورتفوی، Paper، بنیادی، ...).")
        if st.button("📋 خروجی JSON بگیر", key="backup_json",
                     use_container_width=True):
            with st.spinner("در حال ساخت..."):
                ok, msg, counts = export_json()
            if ok:
                st.success(msg)
                with st.expander("جداول صادرشده"):
                    for name, n in counts.items():
                        st.caption(f"• {name}: {n}")
            else:
                st.error(msg)

    st.divider()
    st.subheader("📂 پشتیبان‌های موجود")

    backups = list_backups()
    if not backups:
        st.info("هیچ پشتیبانی وجود ندارد.")
        return

    # Group by kind
    db_backups = [b for b in backups if b["kind"] == "db"]
    json_backups = [b for b in backups if b["kind"] == "json"]

    c1, c2, c3 = st.columns(3)
    c1.metric("کل پشتیبان‌ها", len(backups))
    c2.metric("فایل‌های db", len(db_backups))
    c3.metric("فایل‌های json", len(json_backups))

    st.divider()

    for b in backups:
        with st.container(border=True):
            name_col, size_col, date_col, del_col = st.columns([3, 1, 1, 1])
            icon = "🗄️" if b["kind"] == "db" else "📋"
            name_col.markdown(f"{icon} `{b['name']}`")
            size_col.caption(f"{b['size_kb']} KB")
            date_col.caption(b["modified"].strftime("%m-%d %H:%M"))
            if del_col.button("🗑", key=f"del_{b['name']}"):
                delete_backup(b["name"])
                st.rerun()

    st.divider()
    c1, c2 = st.columns(2)
    with c1:
        keep = st.number_input(
            "نگه‌داشتن آخرین چند نسخه", 3, 50, 10, 1,
            key="backup_keep",
        )
        if st.button("🧹 پاک‌سازی قدیمی‌ها"):
            n = cleanup_old_backups(keep_last_n=int(keep))
            st.success(f"{n} نسخه قدیمی حذف شد")
            st.rerun()
    with c2:
        st.caption(
            "💡 برای بازیابی از پشتیبان db: "
            "Streamlit را متوقف کنید، فایل `.db` را به "
            "`data/market.db` کپی کنید، دوباره اجرا."
        )


def _render_readiness():
    st.caption(
        "این چک‌لیست تعیین می‌کند آیا سیستم برای استفاده با **پول واقعی** "
        "آماده است. **همه** باید ✅ باشند. هیچ‌کدام نباید تعجیل شود."
    )

    s = get_session()
    try:
        rpt = compute_health(s)
    finally:
        s.close()

    # Big status
    if rpt.ready_for_real_money:
        st.success(
            "🎉 **همه شرایط برآورده شده!** اگر ۶ ماه Paper Trading "
            "با نتایج مثبت پایدار داشته‌اید، می‌توانید با **حجم کوچک** "
            "شروع کنید (۱-۲٪ سرمایه، نه بیشتر)."
        )
    else:
        pass_count = sum(1 for r in rpt.readiness if r.status == "pass")
        total = len(rpt.readiness)
        st.warning(
            f"⚠️ **{pass_count} از {total} شرط** برآورده شده. "
            f"برای آمادگی کامل، همه باید ✅ باشند. "
            f"**با پول واقعی شروع نکنید.**"
        )

    st.divider()

    # Checks
    for r in rpt.readiness:
        with st.container(border=True):
            c1, c2, c3 = st.columns([1, 2, 2])
            c1.markdown(
                f"<div style='font-size:22px;'>{_icon(r.status)}</div>",
                unsafe_allow_html=True,
            )
            c2.markdown(f"**{r.name}**")
            c3.markdown(f"مقدار: `{r.value}` — آستانه: `{r.threshold}`")
            if r.action:
                st.caption(f"👆 اقدام: {r.action}")

    st.divider()

    st.markdown("""
### 📋 شرایط استفاده از پول واقعی

**فاز ۱: آمادگی فنی** (این چک‌لیست)
- تمام ۱۰ شرط ✅

**فاز ۲: شروع محافظه‌کارانه**
1. سرمایه واقعی = **حداکثر ۱۰٪** کل سرمایه‌ای که می‌توانید از دست بدهید
2. هر معامله: **۰.۲۵٪** (نه ۰.۵٪)
3. فقط **۱** موقعیت باز در ابتدا
4. حد ضرر **الزامی** — هیچ استثنا
5. بعد از ۱۰ معامله‌ی موفق، ۰.۲۵٪ را به ۰.۵٪ برسانید

**فاز ۳: نظارت**
- هر هفته عملکرد را با Paper مقایسه کنید
- اگر avgR واقعی < Paper avgR − 0.3 → متوقف شوید
- اگر افت سرمایه > ۱۰٪ → متوقف شوید

**❌ هرگز**
- با پول قرض شروع نکنید
- حد ضرر را جابجا نکنید
- بدون backup شروع نکنید
- در بازار قرمز حجم را زیاد نکنید
""")