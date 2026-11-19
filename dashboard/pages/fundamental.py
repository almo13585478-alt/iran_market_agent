"""
Fundamental analysis page: coverage, import, view.
"""

import streamlit as st
from sqlalchemy import select, func
from database.session import get_session
from database.models import Instrument, Fundamental
from fundamental.importer import add_fundamental, import_csv
from fundamental.query import coverage, get_latest


def render(adapter_choice: str):
    st.title("📊 تحلیل بنیادی")
    st.caption(
        "داده‌های بنیادی از CSV یا ورود دستی. TSETMC فیلد بنیادی نمی‌دهد؛ "
        "این داده‌ها را از کدال (دستی) یا فایل تهیه‌شده import کنید."
    )

    s = get_session()
    try:
        tab_cov, tab_import, tab_add, tab_view = st.tabs(
            ["پوشش", "وارد کردن CSV", "افزودن دستی", "مشاهده نماد"]
        )

        with tab_cov:
            cov = coverage(s)
            k1, k2, k3 = st.columns(3)
            k1.metric("نمادهای فعال", cov["total_active"])
            k2.metric("با بنیادی", cov["with_fundamental"])
            k3.metric("پوشش", f"{cov['pct_covered']}%")

            st.divider()
            st.subheader("نمادهای با داده بنیادی")
            rows = s.execute(
                select(Fundamental.symbol, Fundamental.as_of_date,
                       Fundamental.pe, Fundamental.roe, Fundamental.profit_growth)
                .order_by(Fundamental.as_of_date.desc())
                .limit(200)
            ).all()
            if rows:
                st.dataframe(
                    [
                        {"نماد": r[0],
                         "as_of": r[1].strftime("%Y-%m-%d") if r[1] else "-",
                         "P/E": r[2], "ROE": r[3], "رشد سود": r[4]}
                        for r in rows
                    ],
                    width="stretch",
                )
            else:
                st.info("هیچ داده بنیادی ثبت نشده.")

        with tab_import:
            st.caption(
                "ستون‌های لازم: `symbol`, `as_of_date` (YYYY-MM-DD). "
                "اختیاری: `pe`, `pb`, `ps`, `eps`, `roe`, `roa`, `revenue`, "
                "`net_profit`, `debt_ratio`, `profit_growth`, `revenue_growth`, `note`"
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
                        f"اضافه: {res['added']} | رد: {res['skipped']} | "
                        f"خطا: {len(res['errors'])}"
                    )
                    if res["errors"]:
                        with st.expander("خطاها"):
                            for e in res["errors"][:20]:
                                st.markdown(f"- {e}")

            st.divider()
            st.caption("یا متن CSV را پیست کنید:")
            csv_text = st.text_area("CSV", height=120)
            if st.button("وارد کردن از متن") and csv_text.strip():
                res = import_csv(s, csv_text)
                st.success(f"اضافه: {res['added']} | رد: {res['skipped']}")

        with tab_add:
            insts = s.scalars(
                select(Instrument).where(Instrument.is_active.is_(True))
                .order_by(Instrument.symbol).limit(500)
            ).all()
            symbols = [i.symbol for i in insts]
            with st.form("add_fund_form", clear_on_submit=True):
                fc1, fc2 = st.columns(2)
                sym = fc1.selectbox("نماد", symbols, key="fund_sym") if symbols else ""
                date_str = fc2.text_input("تاریخ (YYYY-MM-DD)")
                fc3, fc4, fc5 = st.columns(3)
                pe = fc3.number_input("P/E", 0.0, value=0.0, step=0.1)
                pb = fc4.number_input("P/B", 0.0, value=0.0, step=0.1)
                ps = fc5.number_input("P/S", 0.0, value=0.0, step=0.1)
                fc6, fc7, fc8 = st.columns(3)
                eps = fc6.number_input("EPS", value=0.0, step=1.0)
                roe = fc7.number_input("ROE (%)", value=0.0, step=0.5)
                roa = fc8.number_input("ROA (%)", value=0.0, step=0.5)
                fc9, fc10, fc11 = st.columns(3)
                debt = fc9.number_input("Debt ratio (%)", 0.0, 100.0, 0.0, 0.5)
                pg = fc10.number_input("رشد سود (%)", value=0.0, step=0.5)
                rg = fc11.number_input("رشد فروش (%)", value=0.0, step=0.5)
                note = st.text_input("یادداشت")
                submit = st.form_submit_button("ذخیره")

            if submit and sym and date_str:
                try:
                    from datetime import datetime
                    as_of = datetime.strptime(date_str, "%Y-%m-%d")
                    ok, msg = add_fundamental(
                        s, sym, as_of,
                        pe=pe or None, pb=pb or None, ps=ps or None,
                        eps=eps or None, roe=roe or None, roa=roa or None,
                        debt_ratio=debt or None,
                        profit_growth=pg or None,
                        revenue_growth=rg or None,
                        note=note or None,
                    )
                    (st.success if ok else st.error)(msg)
                except ValueError:
                    st.error("تاریخ نامعتبر — فرمت YYYY-MM-DD")

        with tab_view:
            insts = s.scalars(
                select(Instrument).where(Instrument.is_active.is_(True))
                .order_by(Instrument.symbol).limit(500)
            ).all()
            symbols = [i.symbol for i in insts]
            if not symbols:
                st.info("نمادی در دیتابیس نیست.")
            else:
                pick = st.selectbox("نماد", symbols, key="fund_view")
                rec = get_latest(s, pick)
                if not rec:
                    st.warning(f"بنیادی برای {pick} ثبت نشده.")
                else:
                    st.success(f"as_of: {rec['as_of_date'].strftime('%Y-%m-%d')} "
                               f"| source: {rec['source']}")
                    k1, k2, k3, k4 = st.columns(4)
                    k1.metric("P/E", rec["pe"] or "-")
                    k2.metric("ROE", f"{rec['roe']}%" if rec["roe"] is not None else "-")
                    k3.metric("Debt", f"{rec['debt_ratio']}%" if rec["debt_ratio"] is not None else "-")
                    k4.metric("رشد سود", f"{rec['profit_growth']}%" if rec["profit_growth"] is not None else "-")
                    st.json({k: str(v) if hasattr(v, "isoformat") else v for k, v in rec.items()})
    finally:
        s.close()