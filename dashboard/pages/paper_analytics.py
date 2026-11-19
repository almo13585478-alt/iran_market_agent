"""
Paper Trading analytics page. Read-only dashboard.
"""

import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.session import get_session
from database.models import PaperSignal
from paper_trading.analytics import (
    compute_strategy_analytics, compute_weekly_buckets,
    compute_drawdown_series, signal_quality_vs_outcome, all_strategies,
)


def render(adapter_choice: str):
    st.title("📊 تحلیل Paper Trading")
    st.caption(
        "تحلیل عمیق سیگنال‌های بسته‌شده: streak، expectancy، r-distribution، "
        "drawdown، و کیفیت سیگنال در برابر نتیجه واقعی."
    )

    s = get_session()
    try:
        strategies = all_strategies(s)
        if not strategies:
            st.warning("هیچ Paper Signal بسته‌ای وجود ندارد.")
            return

        selected = st.selectbox(
            "استراتژی", ["همه"] + strategies, key="pa_strategy"
        )
        strat_filter = None if selected == "همه" else selected

        # ---- Header ----
        a = compute_strategy_analytics(s, strat_filter or "ALL")
        if strat_filter is None:
            # aggregate all
            from paper_trading.tracker import paper_stats
            all_stats = paper_stats(s)
            total, wr, avg_r, pf = (
                all_stats["total"], all_stats["win_rate"],
                all_stats["avg_r"], all_stats["profit_factor"],
            )
        else:
            total, wr, avg_r, pf = a.total, a.win_rate, a.avg_r, a.profit_factor

        k1, k2, k3, k4 = st.columns(4)
        k1.metric("معاملات", total)
        k2.metric("نرخ برد", f"{wr}%")
        k3.metric("میانگین R", f"{avg_r:.3f}")
        k4.metric("Profit Factor", f"{pf:.2f}")

        if total < 20:
            st.warning(
                f"⚠️ فقط {total} معامله بسته — برای آمار معنادار حداقل به ۲۰ نیاز دارید."
            )

        # ---- Tabs ----
        tab1, tab2, tab3, tab4, tab5 = st.tabs(
            ["تعمیق استراتژی", "هفتگی", "Drawdown", "توزیع R", "کیفیت سیگنال"]
        )

        with tab1:
            if strat_filter is None:
                st.info("یک استراتژی خاص را انتخاب کنید تا آمار عمیق ببینید.")
            else:
                c1, c2, c3 = st.columns(3)
                c1.metric("Max Win Streak", a.max_win_streak)
                c2.metric("Max Loss Streak", a.max_loss_streak)
                cur_label = (
                    f"{a.current_streak}+" if a.current_streak > 0 else
                    f"{a.current_streak}-" if a.current_streak < 0 else "0"
                )
                c3.metric("Streak فعلی", cur_label)

                c4, c5 = st.columns(2)
                c4.metric("Expectancy", f"{a.expectancy:.3f}")
                c5.metric("مجموع R", f"{a.total_r:+.2f}")

                st.divider()
                st.markdown("**تفسیر:**")
                if a.win_rate > 50 and a.avg_r > 0.2:
                    st.success("آمار سالم — آماده بررسی برای ورود واقعی.")
                elif a.avg_r > 0:
                    st.warning("مثبت ولی ضعیف — نمونه بزرگ‌تر لازم است.")
                else:
                    st.error("edge ندارد — استراتژی نیاز به بازنگری دارد.")

        with tab2:
            weeks = compute_weekly_buckets(s, strat_filter, weeks=12)
            if not weeks:
                st.info("داده هفتگی برای این استراتژی وجود ندارد.")
            else:
                df = pd.DataFrame([
                    {
                        "هفته": w.week_start.strftime("%Y-%m-%d"),
                        "معاملات": w.total,
                        "برد": w.wins,
                        "میانگین R": w.avg_r,
                        "مجموع R": w.total_r,
                    }
                    for w in weeks
                ])
                st.dataframe(df, width="stretch")

                chart_df = pd.DataFrame([
                    {"week": w.week_start, "cum_r": sum(
                        x.total_r for x in weeks[: i + 1]
                    )}
                    for i, w in enumerate(weeks)
                ])
                st.line_chart(chart_df.set_index("week")[["cum_r"]], height=200)

        with tab3:
            dd = compute_drawdown_series(s, strat_filter)
            if not dd:
                st.info("داده‌ای نیست.")
            else:
                df = pd.DataFrame([
                    {"ts": d.ts, "equity": d.equity_r, "dd": d.drawdown_r}
                    for d in dd
                ])
                st.line_chart(df.set_index("ts")[["equity", "dd"]], height=250)
                st.metric("حداکثر افت", f"{max(d.drawdown_r for d in dd):.2f}R")

        with tab4:
            if strat_filter is None:
                st.info("یک استراتژی خاص را انتخاب کنید.")
            else:
                st.markdown("**توزیع R-multiple:**")
                st.dataframe(
                    [
                        {"بازه": k, "تعداد": v}
                        for k, v in a.r_bins.items()
                    ],
                    width="stretch",
                )

        with tab5:
            q = signal_quality_vs_outcome(s)
            st.markdown(f"**نمونه:** {q['n']} معامله")
            if q["correlation"] is None:
                st.info(q["message"])
            else:
                c = q["correlation"]
                st.metric("همبستگی امتیاز ↔ R", f"{c:+.3f}")
                st.markdown(f"**تفسیر:** {q['message']}")
                if c < 0:
                    st.error(
                        "🔴 امتیاز فعلی برعکس کار می‌کند — سیگنال‌های بالاتر "
                        "نتیجه بدتری داشته‌اند. compute_score نیاز به بازنگری دارد."
                    )
                elif c < 0.1:
                    st.warning(
                        "🟡 امتیاز قدرت پیش‌بینی معناداری ندارد. "
                        "بهترین سیگنال‌ها را در `reasons` جست‌وجو کنید."
                    )
    finally:
        s.close()