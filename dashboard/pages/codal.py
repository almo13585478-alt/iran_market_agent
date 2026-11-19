"""Codal page: manual fetch + watch list monitoring + reports feed."""

from datetime import datetime

import streamlit as st
from sqlalchemy import select

from database.session import get_session
from database.models import CodalWatch, CodalReport
from database.audit import log_event
from fundamental.codal_scraper import fetch_and_parse
from fundamental.codal_monitor import (
    add_watch, remove_watch, check_watch, check_all_active,
    unprocessed_reports, mark_processed,
)
from fundamental.importer import add_fundamental


def render(adapter_choice: str):
    st.title("📥 Codal")
    st.caption(
        "⚠️ Codal API رسمی ندارد. این ابزار **بهترین تلاش** است و ممکن است "
        "با تغییر ساختار سایت شکسته شود. هیچ داده‌ای بدون تأیید شما ذخیره نمی‌شود."
    )

    s = get_session()
    try:
        tab_fetch, tab_watch, tab_new, tab_help = st.tabs(
            ["دریافت و پارس", "Watch List", "گزارش‌های جدید", "راهنما"]
        )

        with tab_fetch:
            _render_fetch_tab(s)

        with tab_watch:
            _render_watch_tab(s)

        with tab_new:
            _render_new_tab(s)

        with tab_help:
            st.markdown("""
### راهنمای استفاده

**۱) دریافت دستی یک گزارش:**
- به [codal.ir](https://www.codal.ir/) بروید، گزارش مورد نظر را باز کنید
- URL را از نوار آدرس کپی و در تب «دریافت و پارس» paste کنید

**۲) Watch List (پایش خودکار):**
- یک URL جست‌وجوی Codal برای هر نماد اضافه کنید
- scheduler روزانه ۲۰:۰۰ همه را چک می‌کند
- گزارش‌های جدید در تب «گزارش‌های جدید» ظاهر می‌شوند
- **هیچ گزارشی به‌طور خودکار پارس نمی‌شود** — شما تصمیم می‌گیرید

**نمونه URL جست‌وجو:**
https://www.codal.ir/Companies.aspx?Symbol=فولاد
یا از صفحه یک شرکت در Codal، URL را کپی کنید.

**⚠️ هشدار:** ساختار Codal تغییر می‌کند. اگر watch قدیمی خراب شد، URL را به‌روز کنید.
""")

    finally:
        s.close()


def _render_fetch_tab(s):
    symbols = [
        i.symbol
        for i in s.scalars(
            select(__import__("database.models", fromlist=["Instrument"]).Instrument)
            .where(__import__("database.models", fromlist=["Instrument"]).Instrument.is_active.is_(True))
            .order_by(__import__("database.models", fromlist=["Instrument"]).Instrument.symbol)
            .limit(500)
        ).all()
    ]

    with st.form("codal_fetch"):
        url = st.text_input(
            "URL گزارش در Codal (بدون https:// هم قبول می‌شود)",
            placeholder="https://www.codal.ir/Reports/Decision.aspx?LetterSerial=...",
        )
        symbol = st.selectbox("نماد مرتبط (برای ذخیره)",
                              ["(هیچ)"] + symbols, key="codal_symbol")
        submitted = st.form_submit_button("🔍 دریافت و پارس")

    if submitted and url:
        log_event(s, "system", "codal_fetch_attempt",
                  subject=symbol if symbol != "(هیچ)" else "-",
                  details=url[:200])
        with st.spinner("در حال دریافت..."):
            result = fetch_and_parse(url)

        if result.error:
            st.error(f"خطا: {result.error}")
            return

        color = {"high": "green", "medium": "orange", "low": "red"}[result.confidence]
        st.markdown(
            f"**اعتماد پارس:** :{color}[{result.confidence}] — "
            f"{len(result.parsed)} فیلد ({result.raw_length:,} بایت)"
        )
        for w in result.warnings:
            st.warning(w)

        if not result.parsed:
            st.info("هیچ فیلدی پارس نشد.")
            return

        with st.form("codal_confirm"):
            edited = {}
            cols = st.columns(2)
            for i, (k, v) in enumerate(result.parsed.items()):
                col = cols[i % 2]
                edited[k] = col.number_input(
                    k.upper(), value=float(v),
                    step=0.01 if k in ("pe", "pb", "ps") else 1.0,
                    key=f"codal_{k}",
                )
            as_of = st.date_input("تاریخ as_of", value=datetime.now().date())
            confirm = st.form_submit_button("✅ ذخیره (پس از بررسی)")

        if confirm:
            if symbol == "(هیچ)":
                st.error("نماد را انتخاب کنید.")
                return
            ok, msg = add_fundamental(
                s, symbol,
                as_of_date=datetime.combine(as_of, datetime.min.time()),
                pe=edited.get("pe"), pb=edited.get("pb"), ps=edited.get("ps"),
                eps=edited.get("eps"), roe=edited.get("roe"),
                roa=edited.get("roa"), revenue=edited.get("revenue"),
                net_profit=edited.get("net_profit"),
                debt_ratio=edited.get("debt_ratio"),
                profit_growth=edited.get("profit_growth"),
                revenue_growth=edited.get("revenue_growth"),
                source=f"codal:{result.url[:80]}", note=result.url,
            )
            (st.success if ok else st.error)(msg)


def _render_watch_tab(s):
    watches = s.scalars(
        select(CodalWatch).order_by(CodalWatch.symbol)
    ).all()

    st.subheader(f"پایش {len(watches)} نماد")

    with st.form("add_watch", clear_on_submit=True):
        c1, c2, c3 = st.columns([1, 1, 3])
        wsym = c1.text_input("نماد *").strip()
        wname = c2.text_input("نام شرکت")
        wurl = c3.text_input(
            "URL جست‌وجو *",
            placeholder="https://www.codal.ir/Companies.aspx?Symbol=فولاد",
        )
        add_sub = st.form_submit_button("➕ افزودن")
    if add_sub:
        ok, msg = add_watch(s, wsym, wurl, wname or None)
        (st.success if ok else st.error)(msg)
        if ok:
            st.rerun()

    if not watches:
        st.info("هیچ watch ای اضافه نشده.")
        return

    st.divider()
    if st.button("🔄 چک همه watchهای فعال"):
        with st.spinner("در حال بررسی..."):
            results = check_all_active(s)
        total_new = sum(r["new"] for r in results)
        errors = [r for r in results if r["error"]]
        st.success(f"{len(results)} بررسی شد، {total_new} گزارش جدید، "
                   f"{len(errors)} خطا")
        for e in errors[:10]:
            st.caption(f"❌ {e['symbol']}: {e['error']}")
        st.rerun()

    for w in watches:
        with st.container(border=True):
            cc1, cc2, cc3 = st.columns([3, 1, 1])
            health = "🟢" if w.is_healthy else ("🔴" if w.last_error else "⚪")
            cc1.markdown(f"**{w.symbol}** {health} "
                         f"{' — ' + w.company_name if w.company_name else ''}")
            cc1.caption(f"`{w.search_url[:80]}`")
            if w.last_check_at:
                cc1.caption(f"آخرین چک: {w.last_check_at.strftime('%m-%d %H:%M')} "
                            f"| جدید: {w.new_since_check}")
            if w.last_error:
                cc1.caption(f"🔴 {w.last_error[:100]}")
            b1, b2, b3 = cc2.columns(1) if False else (cc2, cc2, cc2)
            if cc2.button("📥 چک", key=f"chk_{w.id}"):
                with st.spinner("..."):
                    r = check_watch(s, w)
                if r["error"]:
                    st.error(r["error"])
                else:
                    st.success(f"{r['new']} جدید از {r['total_links']} لینک")
                st.rerun()
            if cc2.button("🗑 حذف", key=f"rm_{w.id}"):
                remove_watch(s, w.id)
                st.rerun()


def _render_new_tab(s):
    reports = unprocessed_reports(s, limit=100)
    if not reports:
        st.success("گزارش جدیدی در انتظار بررسی نیست.")
        return

    st.subheader(f"{len(reports)} گزارش جدید")
    for r in reports:
        with st.container(border=True):
            st.markdown(f"**{r.symbol}** — {r.title[:120]}")
            st.caption(f"{r.discovered_at.strftime('%Y-%m-%d %H:%M')} | {r.url[:100]}")
            c1, c2 = st.columns([1, 1])
            if c1.button("✅ علامت‌گذاری به‌عنوان پردازش‌شده",
                         key=f"proc_{r.id}"):
                mark_processed(s, r.id)
                st.rerun()
            if c2.button("📥 fetch و پارس", key=f"fetch_{r.id}"):
                with st.spinner("..."):
                    result = fetch_and_parse(r.url)
                if result.error:
                    st.error(result.error)
                else:
                    st.success(f"{len(result.parsed)} فیلد پارس شد — "
                               f"به تب «دریافت و پارس» بروید و url را paste کنید "
                               f"تا ذخیره کنید.")