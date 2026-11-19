"""Macro indicators page."""

import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.session import get_session
from database.models import MacroIndicator
from macro.importer import add_indicator, import_csv
from macro.query import latest_all, history, pct_change


PRESET_KEYS = [
    ("usd_free", "دلار آزاد", "IRR"),
    ("gold_ounce", "انس جهانی طلا", "USD"),
    ("gold_gram", "طلای ۱۸ عیار", "IRR"),
    ("coin_emami", "سکه امامی", "IRR"),
    ("inflation_yoy", "تورم سالانه", "%"),
    ("interest_rate", "نرخ بهره بانکی", "%"),
]


def render(adapter_choice: str):
    st.title("🌍 داده‌های کلان")
    st.caption(
        "داده‌های کلان را دستی یا با CSV وارد کنید. CBI و سایت‌های قیمت ارز "
        "API پایدار ندارند؛ به همین دلیل از ورود دستی استفاده می‌کنیم."
    )

    s = get_session()
    try:
        tab_summary, tab_add, tab_import, tab_history = st.tabs(
            ["📊 خلاصه", "➕ افزودن دستی", "📥 CSV", "📈 تاریخچه"]
        )

        with tab_summary:
            latest = latest_all(s)
            if not latest:
                st.info(
                    "هیچ داده‌ای ثبت نشده. از تب‌های «افزودن دستی» یا "
                    "«CSV» شروع کنید."
                )
            else:
                cols = st.columns(min(3, len(latest)))
                for i, (key, item) in enumerate(latest.items()):
                    col = cols[i % len(cols)]
                    unit = item["unit"]
                    val = item["value"]
                    if unit == "%":
                        display = f"{val:.1f}%"
                    elif unit == "IRR" and val > 1_000_000:
                        display = f"{val / 1_000_000:.2f}M"
                    else:
                        display = f"{val:,.0f}"
                    days = item["days_ago"]
                    caption = f"{days} روز پیش" if days > 0 else "امروز"
                    pct_30d = pct_change(s, key, days=30)
                    delta = f"{pct_30d:+.1f}% (۳۰ روز)" if pct_30d is not None \
                        else None
                    col.metric(
                        item["label"],
                        display,
                        delta=delta,
                        help=f"واحد: {unit} | منبع: {item['source']} | {caption}",
                    )

                # Freshness warnings
                stale = [
                    item for item in latest.values() if item["days_ago"] > 7
                ]
                if stale:
                    st.warning(
                        f"⚠️ {len(stale)} شاخص بیش از ۷ روز است به‌روز نشده. "
                        f"از تب «افزودن دستی» مقادیر جدید را وارد کنید."
                    )

        with tab_add:
            key_options = [k for k, _, _ in PRESET_KEYS] + ["custom"]
            labels = {k: lbl for k, lbl, _ in PRESET_KEYS}
            units = {k: u for k, _, u in PRESET_KEYS}

            with st.form("macro_add_form", clear_on_submit=True):
                col1, col2 = st.columns(2)
                preset = col1.selectbox(
                    "شاخص", key_options,
                    format_func=lambda k: labels.get(k, "— سفارشی —"),
                )

                if preset == "custom":
                    custom_key = col1.text_input("key (انگلیسی)").strip()
                    custom_label = col2.text_input("برچسب فارسی").strip()
                    custom_unit = col2.text_input("واحد", value="IRR")
                else:
                    custom_key = preset
                    custom_label = labels[preset]
                    custom_unit = units[preset]

                col3, col4 = st.columns(2)
                value = col3.number_input("مقدار", min_value=0.0, step=1000.0)
                date = col4.date_input("تاریخ")

                notes = st.text_input("یادداشت (اختیاری)")
                submitted = st.form_submit_button("ذخیره")

            if submitted and custom_key and custom_label and value > 0:
                from datetime import datetime
                as_of = datetime.combine(date, datetime.min.time())
                ok, msg = add_indicator(
                    s, custom_key, custom_label, float(value),
                    unit=custom_unit, as_of_date=as_of,
                    source="manual", notes=notes or None,
                )
                (st.success if ok else st.error)(msg)
                if ok:
                    st.rerun()

        with tab_import:
            st.caption(
                "ستون‌های لازم: `key`, `label`, `value`, `unit`, `as_of_date` "
                "(YYYY-MM-DD). اختیاری: `source`, `notes`."
            )
            st.code(
                "key,label,value,unit,as_of_date,source,notes\n"
                "usd_free,دلار آزاد,900000,IRR,2025-06-30,manual,\n"
                "inflation_yoy,تورم سالانه,35.5,%,2025-06-01,cbi,\n",
                language="text",
            )
            uploaded = st.file_uploader("فایل CSV", type=["csv"])
            if uploaded is not None:
                try:
                    text = uploaded.read().decode("utf-8-sig")
                except UnicodeDecodeError:
                    text = uploaded.read().decode("cp1256")
                if st.button("وارد کردن"):
                    res = import_csv(s, text)
                    st.success(
                        f"اضافه: {res['added']} | بروز: {res['updated']} | "
                        f"خطا: {len(res['errors'])}"
                    )
                    if res["errors"]:
                        with st.expander("خطاها"):
                            for e in res["errors"][:20]:
                                st.markdown(f"- {e}")

            csv_text = st.text_area("یا متن CSV:", height=150)
            if st.button("وارد کردن از متن") and csv_text.strip():
                res = import_csv(s, csv_text)
                st.success(
                    f"اضافه: {res['added']} | بروز: {res['updated']}"
                )

        with tab_history:
            latest = latest_all(s)
            if not latest:
                st.info("هیچ داده‌ای نیست.")
            else:
                key = st.selectbox(
                    "شاخص", list(latest.keys()),
                    format_func=lambda k: latest[k]["label"],
                )
                days = st.slider("تعداد روز گذشته", 30, 365, 90, 30)
                hist = history(s, key, days=days)
                if len(hist) < 2:
                    st.info(f"فقط {len(hist)} نقطه داده — نمودار نیاز به ۲+ دارد.")
                else:
                    df = pd.DataFrame([
                        {"date": h["as_of_date"], "value": h["value"]}
                        for h in hist
                    ])
                    st.line_chart(df.set_index("date")[["value"]], height=280)

                    ch = pct_change(s, key, days=days)
                    if ch is not None:
                        st.metric(
                            f"تغییر {days} روزه",
                            f"{ch:+.2f}%",
                            delta=f"از {hist[0]['value']:,.0f} به {hist[-1]['value']:,.0f}",
                        )
    finally:
        s.close()