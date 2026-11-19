"""
Home page: daily briefing + decision + overview + market state.
"""

import pandas as pd
import streamlit as st
from sqlalchemy import select, func, desc

from database.session import get_session
from database.models import Instrument, PriceBar, DataSourceStatus
from analysis.technical import analyze_instrument
from analysis.market_state import classify_market
from decision_engine.scoring import compute_score, rank_universe
from decision_engine.signal_generator import generate_signal
from backtesting.group_backtest import max_bars_available
from news.query import negative_symbols_today, recent_news
from paper_trading.tracker import paper_stats
from alerts.engine import check_alerts
from fundamental.query import get_latest as get_fundamental
from ai_assistant.briefing import generate_briefing, enhance_with_llm
from ai_assistant.llm import is_configured as llm_configured
from dashboard.chart_helpers import market_gauge, sparkline
from dashboard.components import render_signal_card, warning_banner


def render(portfolio_value: float, risk_pct: float, min_rr: float) -> None:
    st.title("🏠 خانه")

    tab_brief, tab_decision, tab_overview, tab_market = st.tabs([
        "📋 بریفینگ امروز",
        "🎯 تصمیم امروز",
        "📊 نمای کلی",
        "🌡️ وضعیت بازار",
    ])

    with tab_brief:
        _render_briefing()

    with tab_decision:
        _render_decision(portfolio_value, risk_pct, min_rr)

    with tab_overview:
        _render_overview()

    with tab_market:
        _render_market()


# ===========================================================================
# Briefing tab
# ===========================================================================

def _render_briefing():
    s = get_session()
    try:
        briefing = generate_briefing(s)
    finally:
        s.close()

    st.caption(
        f"تولید شده در {briefing.generated_at.strftime('%Y-%m-%d %H:%M')}"
    )

    # Summary
    st.markdown(
        f"<div style='background:rgba(33,150,243,0.08);"
        f"border-right:4px solid #2196f3;padding:16px;"
        f"border-radius:8px;margin:12px 0;font-size:15px;line-height:1.9;'>"
        f"{briefing.summary_text}"
        f"</div>",
        unsafe_allow_html=True,
    )

    for w in briefing.warnings:
        warning_banner(w, "warn")

    # Actions
    st.subheader("🎯 کارهای پیشنهادی امروز")
    for i, action in enumerate(briefing.action_items, 1):
        st.markdown(f"**{i}.** {action}")

    # Metrics row
    st.divider()
    k1, k2, k3, k4 = st.columns(4)
    state = briefing.market.get("state", "unknown")
    k1.metric(
        "وضعیت بازار",
        {"green": "🟢", "yellow": "🟡", "red": "🔴"}.get(state, "⚪"),
        delta=f"امتیاز {briefing.market.get('score', 0):.0f}",
    )
    k2.metric("هشدار بحرانی", briefing.alerts.get("critical", 0))
    k3.metric("Paper باز", briefing.paper.get("open", 0))
    k4.metric(
        "موقعیت‌های پرتفوی", briefing.portfolio.get("n_positions", 0),
    )

    # NEW: Risk + Benchmark row
    if briefing.portfolio.get("n_positions", 0) > 0:
        st.divider()
        st.subheader("🛡️ ریسک و عملکرد")
        r_col, b_col = st.columns(2)

        with r_col:
            risk = briefing.risk
            rk1, rk2 = st.columns(2)
            rk1.metric(
                "VaR روزانه",
                f"{risk.get('daily_var_pct_worst', 0)}%",
                help=f"~{risk.get('daily_var_95_worst', 0):,.0f} ریال در یک روز بد (۹۵٪)",
            )
            rk2.metric(
                "بودجه ریسک",
                f"{risk.get('current_open_risk_pct', 0)}%",
                delta=f"باقی {risk.get('remaining_risk_budget_pct', 0)}%",
                delta_color="normal",
            )
            rk3, rk4 = st.columns(2)
            rk3.metric(
                "تمرکز (HHI)",
                f"{risk.get('hhi', 0):.0f}",
                help="بالای ۳۰۰۰ = تمرکز بالا",
            )
            rk4.metric(
                "بزرگ‌ترین صنعت",
                f"{risk.get('top_industry_pct', 0)}%",
            )
            if risk.get("warnings"):
                for w in risk["warnings"][:3]:
                    st.caption(f"⚠️ {w}")

        with b_col:
            bm = briefing.benchmark
            if bm.get("alpha_pct") is not None:
                verdict_icon = {
                    "outperforming": "🟢",
                    "underperforming": "🔴",
                    "tracking": "⚖️",
                }.get(bm.get("verdict"), "⚪")
                st.markdown(f"### {verdict_icon} مقایسه با شاخص")
                bm1, bm2 = st.columns(2)
                bm1.metric(
                    "پرتفوی",
                    f"{bm.get('portfolio_return_pct', 0):+.2f}%",
                )
                bm2.metric(
                    "شاخص هم‌وزن",
                    f"{bm.get('index_return_pct', 0):+.2f}%",
                )
                st.metric(
                    "آلفا (بازده اضافی)",
                    f"{bm.get('alpha_pct', 0):+.2f}%",
                    delta=f"{bm.get('days_tracked', 0)} روز",
                    delta_color="normal",
                )
                st.caption(bm.get("summary", ""))
            else:
                st.info(
                    "برای مقایسه با شاخص، از صفحه «📋 گزارش روزانه» "
                    "snapshot ثبت کنید."
                )

    # Macro strip
    if briefing.macro.get("items"):
        st.divider()
        st.subheader("💵 داده‌های کلان")
        macro_cols = st.columns(min(4, len(briefing.macro["items"])))
        for i, (key, item) in enumerate(briefing.macro["items"].items()):
            col = macro_cols[i % len(macro_cols)]
            unit = item["unit"]
            val = item["value"]
            if unit == "%":
                display = f"{val:.1f}%"
            elif unit == "IRR" and val > 1_000_000:
                display = f"{val / 1_000_000:.2f}M"
            else:
                display = f"{val:,.0f}"
            col.metric(item["label"], display)

    # Data health
    if briefing.data_health.get("message"):
        st.divider()
        st.subheader("📊 سلامت داده")
        st.caption(briefing.data_health["message"])
        if briefing.data_health.get("stale_prices"):
            st.markdown("**قیمت‌های قدیمی:**")
            for sym, date in briefing.data_health["stale_prices"]:
                st.markdown(f"- {sym}: آخرین {date}")
        if briefing.data_health.get("stale_fundamentals"):
            st.markdown("**بنیادی قدیمی:**")
            for sym, date in briefing.data_health["stale_fundamentals"]:
                st.markdown(f"- {sym}: آخرین {date}")

    # Expandables
    st.divider()
    with st.expander("📊 جزئیات بازار"):
        m = briefing.market
        c1, c2, c3 = st.columns(3)
        c1.metric("عرض امروز", f"{m.get('breadth_today', 0)}%")
        c2.metric("بالای EMA50", f"{m.get('trend_breadth', 0)}%")
        c3.metric("نسبت حجم", f"{m.get('vol_ratio', 1.0)}×")
        for r in m.get("reasons", []):
            st.markdown(f"- {r}")

    if briefing.alerts.get("top"):
        with st.expander("🔔 هشدارهای برتر"):
            for a in briefing.alerts["top"]:
                icon = {"critical": "🔴", "warning": "🟠",
                        "info": "🔵"}.get(a["level"], "⚪")
                st.markdown(f"{icon} {a['message']}")

    if briefing.flow_highlights:
        with st.expander("🌊 تابلوخوانی امروز"):
            st.dataframe(
                [
                    {
                        "نماد": f["symbol"],
                        "حقوقی خالص (سهم)": f"{f['inst_net']:+,.0f}",
                        "قدرت خریدار": f"{f['buy_power']:.2f}×",
                    }
                    for f in briefing.flow_highlights
                ],
                width="stretch",
            )

    if briefing.news.get("recent_titles"):
        with st.expander("📰 اخبار اخیر"):
            for n in briefing.news["recent_titles"]:
                icon = ("🟢" if n["sentiment"] > 0.2 else
                        "🔴" if n["sentiment"] < -0.2 else "⚪")
                st.markdown(f"{icon} `{n['published']}` {n['title']}")

    # Optional LLM enhance
    st.divider()
    if llm_configured():
        if st.button("✨ بهبود بریفینگ با AI"):
            s = get_session()
            try:
                enhanced = enhance_with_llm(briefing, s)
            finally:
                s.close()
            st.markdown(
                f"<div style='background:rgba(76,175,80,0.08);"
                f"border-right:4px solid #4caf50;padding:16px;"
                f"border-radius:8px;line-height:1.9;'>{enhanced}</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption(
            "💡 برای بریفینگ با AI، `LLM_API_KEY` را در `.env` تنظیم کنید."
        )

# ===========================================================================
# Decision tab (unchanged)
# ===========================================================================

def _render_decision(portfolio_value, risk_pct, min_rr):
    s = get_session()
    try:
        ms = classify_market(s)
        st.session_state["market_state"] = ms
        alerts = check_alerts(s)
        ps = paper_stats(s)
        neg = negative_symbols_today(s, days=3)
    finally:
        s.close()

    critical = [a for a in alerts if a.level == "critical"]
    warning = [a for a in alerts if a.level == "warning"]

    g_col, k_col = st.columns([1, 2])

    with g_col:
        fig = market_gauge(ms.state, ms.score)
        st.plotly_chart(
            fig, use_container_width=True,
            config={"displayModeBar": False},
            key="home_gauge",
        )

    with k_col:
        k1, k2, k3 = st.columns(3)
        k1.metric("🔴 بحرانی", len(critical))
        k2.metric("🟠 اخطار", len(warning))
        k3.metric("📝 Paper باز", ps["open"])

        k4, k5, k6 = st.columns(3)
        k4.metric("📰 خبر منفی", len(neg))
        k5.metric("Paper بسته", ps["total"])
        k6.metric("نرخ برد", f"{ps['win_rate']}%")

        if ms.state == "red":
            st.error(
                "🔴 **بازار قرمز** — حجم پوزیشن‌های جدید را نصف کنید."
            )
        elif ms.state == "green":
            st.success("🟢 **بازار سبز** — شرایط مساعد است.")
        else:
            st.warning("🟡 **بازار زرد** — با احتیاط عمل کنید.")

    st.divider()
    st.subheader("🎯 سیگنال‌های امروز")
    _render_top_signals(portfolio_value, risk_pct, min_rr, ms.state)

    st.divider()
    a_col, n_col = st.columns(2)

    with a_col:
        st.subheader("🔔 هشدارهای فعال")
        if not alerts:
            st.success("هیچ هشدار فعالی نیست ✅")
        else:
            for a in alerts[:8]:
                icon = {"critical": "🔴", "warning": "🟠",
                        "info": "🔵"}.get(a.level, "⚪")
                st.markdown(f"{icon} {a.message}")

    with n_col:
        st.subheader("📰 اخبار اخیر")
        s = get_session()
        try:
            recent = recent_news(s, limit=6)
        finally:
            s.close()
        if not recent:
            st.info("خبری ثبت نشده.")
        else:
            for n in recent:
                if n.sentiment_score > 0.2:
                    icon = "🟢"
                elif n.sentiment_score < -0.2:
                    icon = "🔴"
                else:
                    icon = "⚪"
                st.markdown(
                    f"{icon} `{n.published_at.strftime('%m-%d')}` "
                    f"{n.title[:80]}"
                )


def _render_top_signals(portfolio_value, risk_pct, min_rr, market_state_str):
    rankings = st.session_state.get("rankings", [])

    if not rankings:
        warning_banner(
            "هنوز رتبه‌بندی محاسبه نشده. می‌توانید در صفحه «📈 قیمت و تحلیل» "
            "همه نمادها را تحلیل کنید، یا اینجا روی دکمه زیر بزنید.",
            "info",
        )
        if st.button("⚡ تحلیل سریع ۳۰ نماد", key="home_quick_scan"):
            _quick_scan()
            st.rerun()
        return

    s = get_session()
    try:
        neg_map = {n["symbol"]: n for n in negative_symbols_today(s, days=3)}

        signals = []
        for r in rankings[:20]:
            sig = generate_signal(
                r,
                portfolio_value=portfolio_value,
                risk_per_trade_pct=risk_pct,
                min_rr=min_rr,
                market_state=None,
                recent_negative_news=(r.symbol in neg_map),
            )
            if sig.action == "BUY_CANDIDATE":
                signals.append((sig, r))

        if not signals:
            warning_banner(
                "هیچ نمادی در بین ۲۰ نماد برتر شرایط خرید کامل ندارد. "
                "نقد ماندن معتبر است.",
                "warn",
            )
            return

        st.caption(f"{len(signals)} سیگنال خرید معتبر")

        top = signals[:3]
        cols = st.columns(len(top)) if len(top) > 1 else [st.container()]
        for col, (sig, score) in zip(cols, top):
            with col:
                df = _load_df_for_symbol(s, sig.symbol, bars=60)
                render_signal_card(sig, score, df)
    finally:
        s.close()


def _quick_scan():
    s = get_session()
    try:
        rows = s.execute(
            select(Instrument.symbol, func.count(PriceBar.id).label("n"))
            .join(PriceBar, PriceBar.instrument_id == Instrument.id)
            .where(Instrument.is_active.is_(True))
            .group_by(Instrument.id)
            .having(func.count(PriceBar.id) >= 60)
            .order_by(desc("n"))
            .limit(30)
        ).all()

        progress = st.progress(0.0, text="تحلیل...")
        results = []
        for idx, (sym, _n) in enumerate(rows, 1):
            a = analyze_instrument(s, sym)
            if a.get("ok"):
                fund = get_fundamental(s, sym)
                results.append(compute_score(sym, a, fundamental=fund))
            progress.progress(idx / len(rows), text=f"{idx}/{len(rows)} — {sym}")
        progress.empty()

        results = rank_universe(results)
        results.sort(key=lambda x: -x.total)
        st.session_state["rankings"] = results
    finally:
        s.close()


def _load_df_for_symbol(session, symbol: str, bars: int = 60):
    inst = session.scalar(
        select(Instrument).where(Instrument.symbol == symbol)
    )
    if inst is None:
        return None
    rows = (
        session.query(PriceBar)
        .filter(PriceBar.instrument_id == inst.id)
        .order_by(PriceBar.ts.desc())
        .limit(bars)
        .all()
    )
    if not rows:
        return None
    return pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high,
         "low": b.low, "close": b.close,
         "volume": b.volume, "value": b.value}
        for b in reversed(rows)
    ])


# ===========================================================================
# Overview tab (unchanged)
# ===========================================================================

def _render_overview():
    s = get_session()
    try:
        total_inst = s.scalar(select(func.count()).select_from(Instrument)) or 0
        active_inst = s.scalar(
            select(func.count()).select_from(Instrument)
            .where(Instrument.is_active.is_(True))
        ) or 0
        price_count = s.scalar(select(func.count()).select_from(PriceBar)) or 0
        priced_inst = s.scalar(
            select(func.count(func.distinct(PriceBar.instrument_id)))
        ) or 0
        max_b = max_bars_available(s)

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("کل نمادها", f"{total_inst:,}")
        c2.metric("فعال", f"{active_inst:,}")
        c3.metric("رکورد قیمت", f"{price_count:,}")
        c4.metric("حداکثر رکورد", f"{max_b:,}")

        if max_b < 62:
            st.warning(f"⚠️ حداکثر {max_b} رکورد، بک‌تست به ۶۲+ نیاز دارد.")

        st.divider()
        st.subheader("وضعیت منابع داده")
        sources = s.scalars(select(DataSourceStatus)).all()
        if sources:
            st.dataframe(
                [
                    {
                        "منبع": x.source_name,
                        "سالم": "✅" if x.is_healthy else "❌",
                        "آخرین موفقیت": x.last_success_at.strftime("%Y-%m-%d %H:%M")
                        if x.last_success_at else "-",
                        "خطا": (x.last_error or "-")[:100],
                    }
                    for x in sources
                ],
                width="stretch",
            )
    finally:
        s.close()


# ===========================================================================
# Market state tab (unchanged)
# ===========================================================================

def _render_market():
    st.caption(
        "طبقه‌بندی بازار بر اساس ۵ سیگنال: عرض امروز، عرض ۵ روز، "
        "روند EMA50، نسبت حجم، نوسان عرضی."
    )
    s = get_session()
    try:
        if st.button("🔄 محاسبه وضعیت بازار", key="mkt_calc_home"):
            st.session_state["market_state"] = classify_market(s)

        ms = st.session_state.get("market_state")
        if not ms:
            st.info("دکمه محاسبه را بزنید.")
            return

        state_fa = {"green": "🟢 سبز", "yellow": "🟡 زرد",
                    "red": "🔴 قرمز"}.get(ms.state, ms.state)
        st.markdown(f"### وضعیت: {state_fa}")
        st.caption(
            f"امتیاز: **{ms.score}/100** — "
            f"{ms.computed_at.strftime('%H:%M')}"
        )

        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("عرض امروز", f"{ms.breadth_today}%")
        k2.metric("عرض ۵ روز", f"{ms.breadth_5d}%")
        k3.metric("بالای EMA50", f"{ms.trend_breadth}%")
        k4.metric("نسبت حجم", f"{ms.vol_ratio}×")
        k5.metric("نوسان عرضی", f"{ms.vol_cross}%")

        st.divider()
        st.subheader("دلایل")
        for r in ms.reasons:
            st.markdown(f"- {r}")

        st.divider()
        st.subheader("اقدامات سیستم")
        st.markdown(
            f"- ضریب حجم: **{ms.size_multiplier}×**\n"
            f"- افزایش آستانه: **+{ms.score_bonus:.1f}**\n"
            f"- اجازه معامله: **{'بله' if ms.trade_allowed else 'خیر'}**"
        )
        if ms.state == "red":
            st.error("🔴 در بازار قرمز، حجم نصف و آستانه بالاتر.")
        elif ms.state == "green":
            st.success("🟢 شرایط مساعد.")
    finally:
        s.close()