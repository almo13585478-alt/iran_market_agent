"""Auto walk-forward configuration page."""

from datetime import datetime

import pandas as pd
import streamlit as st

from database.session import get_session
from backtesting.auto_walk_forward import (
    accept_manually, get_active, get_history, run_auto_walk_forward,
)


def render(adapter_choice: str):
    st.title("📊 Walk-Forward خودکار")
    st.caption(
        "هر جمعه ۱۸:۰۰ به‌طور خودکار ۲۷ ترکیب تست می‌شود و بهترین "
        "robust ذخیره می‌شود. **تغییر به‌صورت خودکار اعمال نمی‌شود** — "
        "این صفحه پیشنهاد می‌دهد و شما تأیید می‌کنید."
    )

    s = get_session()
    try:
        tab_current, tab_run, tab_history = st.tabs(
            ["🎯 پیکربندی فعال", "▶️ اجرای دستی", "📜 تاریخچه"]
        )

        with tab_current:
            _render_active(s)

        with tab_run:
            _render_run(s)

        with tab_history:
            _render_history(s)
    finally:
        s.close()


def _render_active(s):
    active = get_active(s, "v2b")
    if active is None:
        st.info(
            "هیچ پیکربندی فعالی وجود ندارد. "
            "از تب «▶️ اجرای دستی» شروع کنید."
        )
        return

    st.markdown(
        f"<div style='background:rgba(38,166,154,0.1);"
        f"border-right:4px solid #26a69a;padding:20px;"
        f"border-radius:8px;'>"
        f"<div style='font-size:12px;color:#aaa;'>"
        f"فعال از {active.applied_at.strftime('%Y-%m-%d %H:%M') if active.applied_at else '-'}"
        f"</div>"
        f"<div style='font-size:24px;font-weight:700;margin-top:6px;'>"
        f"thr={active.threshold} · ATR={active.atr_mult}× · R/R={active.rr_ratio}"
        f"</div></div>",
        unsafe_allow_html=True,
    )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("TRAIN avgR", f"{active.train_avg_r:.3f}",
              delta=f"{active.train_trades} معامله")
    c2.metric("TEST avgR", f"{active.test_avg_r:.3f}",
              delta=f"{active.test_trades} معامله")
    c3.metric("TEST نرخ برد", f"{active.test_win_rate:.1f}%")
    c4.metric("TEST PF", f"{active.test_profit_factor:.2f}")

    st.caption(
        f"افت (degradation): {active.degradation:.2f} | "
        f"حکم: **{active.verdict}**"
    )
    if active.notes:
        st.caption(f"یادداشت: {active.notes}")

    st.divider()
    st.info(
        "💡 **این پیکربندی به‌طور خودکار در استراتژی‌ها اعمال نمی‌شود.** "
        "برای استفاده، در صفحه «📝 Paper Trading» پارامترهای "
        f"`min_soft=?, atr={active.atr_mult}, rr={active.rr_ratio}` را "
        "دستی وارد کنید."
    )


def _render_run(s):
    st.caption(
        "اجرای دستی ۲۷ ترکیب روی ۲۰ نماد. حدود ۳-۵ دقیقه طول می‌کشد."
    )

    c1, c2 = st.columns(2)
    n_symbols = c1.number_input(
        "تعداد نماد", 5, 50, 20, 5, key="auto_wf_nsym",
    )
    strategy = c2.selectbox(
        "استراتژی", ["v2b"], key="auto_wf_strat",
    )

    if st.button("▶️ اجرای Walk-Forward", key="auto_wf_run"):
        prog = st.progress(0.0, text="شروع...")

        def _cb(i, n, label):
            prog.progress(i / max(n, 1), text=f"{i}/{n} — {label}")

        with st.spinner("در حال اجرا..."):
            result = run_auto_walk_forward(
                s, strategy=strategy, n_symbols=int(n_symbols),
                progress_cb=_cb,
            )

        prog.empty()

        if not result.ok:
            st.error(result.message)
            return

        st.success(result.message)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("کل ترکیب", result.n_combos)
        c2.metric("Robust", result.n_robust)
        c3.metric("Fragile", result.n_fragile)
        c4.metric("Overfit", result.n_overfit)

        if result.best_combo:
            st.divider()
            st.subheader("بهترین ترکیب این اجرا")
            bc1, bc2, bc3 = st.columns(3)
            bc1.metric("آستانه", result.best_combo["threshold"])
            bc2.metric("ATR×", result.best_combo["atr_mult"])
            bc3.metric("R/R", result.best_combo["rr_ratio"])
            sc1, sc2, sc3 = st.columns(3)
            sc1.metric("TEST avgR", f"{result.best_combo['test_avg_r']:.3f}")
            sc2.metric("TEST نرخ برد", f"{result.best_combo['test_win_rate']}%")
            sc3.metric("TEST معاملات", result.best_combo["test_trades"])

        if result.swapped:
            st.success(
                "✅ پیکربندی فعال به‌طور خودکار به این ترکیب تغییر کرد "
                "(چون بهتر از فعال قبلی بود)."
            )
        elif result.warning:
            st.warning(f"⚠️ {result.warning}")

        if result.n_robust == 0:
            st.info(
                "هیچ ترکیب robust پیدا نشد. "
                "معنایش این است که روی داده فعلی، استراتژی edge پایدار ندارد. "
                "این پیام صادقانه است — تعجیل نکنید."
            )


def _render_history(s):
    history = get_history(s, "v2b", limit=200)
    if not history:
        st.info("هنوز هیچ اجرایی ثبت نشده.")
        return

    st.caption(f"{len(history)} ردیف (آخرین ۲۰۰)")

    # Accepted-only view
    accepted = [h for h in history if h.is_active or h.applied_at is not None]
    if accepted:
        st.subheader("پیکربندی‌های پذیرفته‌شده")
        df_acc = pd.DataFrame([
            {
                "زمان": h.computed_at.strftime("%Y-%m-%d %H:%M"),
                "آستانه": h.threshold,
                "ATR×": h.atr_mult,
                "R/R": h.rr_ratio,
                "TEST avgR": h.test_avg_r,
                "TEST برد": f"{h.test_win_rate}%",
                "TEST معاملات": h.test_trades,
                "فعال فعلی": "✅" if h.is_active else "",
            }
            for h in accepted[:20]
        ])
        st.dataframe(df_acc, width="stretch", hide_index=True)

        # Trend of accepted
        if len(accepted) >= 2:
            chart_df = pd.DataFrame([
                {"زمان": h.computed_at, "TEST avgR": h.test_avg_r}
                for h in reversed(accepted[:30])
            ])
            st.line_chart(
                chart_df.set_index("زمان")[["TEST avgR"]], height=220,
            )

    st.divider()
    st.subheader("همه ردیف‌ها")
    df = pd.DataFrame([
        {
            "زمان": h.computed_at.strftime("%m-%d %H:%M"),
            "thr": h.threshold,
            "atr": h.atr_mult,
            "rr": h.rr_ratio,
            "TRAIN avgR": h.train_avg_r,
            "TEST avgR": h.test_avg_r,
            "TEST برد": f"{h.test_win_rate}%",
            "افت": h.degradation,
            "حکم": {
                "robust": "🟢", "fragile": "🟡",
                "overfit": "🔴", "unknown": "⚪",
            }.get(h.verdict, "⚪"),
            "فعال": "✅" if h.is_active else "",
        }
        for h in history[:100]
    ])
    st.dataframe(df, width="stretch", hide_index=True)

    # Manual accept
    st.divider()
    st.subheader("پذیرش دستی یک ترکیب")
    st.caption(
        "اگر می‌خواهید ترکیبی را بدون رعایت قاعده «بهتر از فعال فعلی» "
        "فعال کنید."
    )
    options = {
        f"{h.threshold} / {h.atr_mult}× / {h.rr_ratio} "
        f"(test avgR={h.test_avg_r:.3f}, {h.computed_at.strftime('%m-%d %H:%M')})": h.id
        for h in history[:30] if h.verdict == "robust"
    }
    if not options:
        st.info("هیچ ترکیب robust در ۳۰ ردیف آخر نیست.")
    else:
        pick = st.selectbox(
            "ترکیب", list(options.keys()), key="auto_wf_manual_pick",
        )
        if st.button("✅ فعال کن", key="auto_wf_manual_apply"):
            ok, msg = accept_manually(s, options[pick])
            (st.success if ok else st.error)(msg)
            if ok:
                st.rerun()