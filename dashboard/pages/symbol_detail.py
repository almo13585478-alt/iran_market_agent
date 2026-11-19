"""
Symbol detail page: candlestick + indicators + signal + score + order flow.
"""

from datetime import datetime

import pandas as pd
import streamlit as st
from sqlalchemy import select

from database.session import get_session
from database.models import Instrument, PriceBar
from analysis.technical import analyze_instrument
from analysis.market_state import classify_market
from analysis.order_flow import analyze as analyze_order_flow, bulk_sync
from decision_engine.scoring import compute_score
from decision_engine.signal_generator import generate_signal
from news.query import negative_symbols_today, recent_news
from fundamental.query import get_latest as get_fundamental
from dashboard.chart_helpers import candlestick_with_setup
from dashboard.components import (
    render_signal_card, status_pill, warning_banner,
)


def render(portfolio_value: float, risk_pct: float, min_rr: float) -> None:
    st.title("🔍 تحلیل نماد")
    st.caption(
        "نمودار کامل، اندیکاتورها، سیگنال، تابلوخوانی. "
        "همه‌ی داده از دیتابیس محلی است (بدون تماس شبکه‌ای)."
    )

    s = get_session()
    try:
        symbols = [
            r[0]
            for r in s.execute(
                select(Instrument.symbol)
                .where(Instrument.is_active.is_(True))
                .order_by(Instrument.symbol)
                .limit(500)
            ).all()
        ]
    finally:
        s.close()

    if not symbols:
        warning_banner("هیچ نمادی در دیتابیس نیست.", "bad")
        return

    if "_force_symbol" in st.session_state:
        _forced = st.session_state.pop("_force_symbol")
        if _forced in symbols:
            st.session_state["sd_pick"] = _forced
            st.session_state["detail_symbol"] = _forced

    if "detail_symbol" not in st.session_state:
        st.session_state["detail_symbol"] = symbols[0]
    if st.session_state["detail_symbol"] not in symbols:
        st.session_state["detail_symbol"] = symbols[0]

    c1, c2, c3 = st.columns([2, 1, 1])
    with c1:
        preset = st.session_state["detail_symbol"]
        idx = symbols.index(preset) if preset in symbols else 0
        pick = st.selectbox("نماد", symbols, index=idx, key="sd_pick")
        st.session_state["detail_symbol"] = pick

    with c2:
        bars_count = st.number_input(
            "تعداد کندل", 60, 500, 200, 20, key="sd_bars",
        )

    with c3:
        st.write("")
        st.write("")
        if st.button("🔄 تحلیل", key="sd_run", use_container_width=True):
            st.rerun()

    st.divider()

    s = get_session()
    try:
        inst = s.scalar(
            select(Instrument).where(Instrument.symbol == pick)
        )
        if inst is None:
            st.error(f"نماد {pick} پیدا نشد.")
            return

        bars = (
            s.query(PriceBar)
            .filter(PriceBar.instrument_id == inst.id)
            .order_by(PriceBar.ts.desc())
            .limit(int(bars_count))
            .all()
        )
        if not bars:
            st.warning(f"برای {pick} هیچ قیمتی ثبت نشده. از سایدبار sync کنید.")
            return

        df = pd.DataFrame([
            {"ts": b.ts, "open": b.open, "high": b.high,
             "low": b.low, "close": b.close,
             "volume": b.volume, "value": b.value}
            for b in reversed(bars)
        ])

        analysis = analyze_instrument(s, pick)
        if not analysis.get("ok"):
            st.warning(f"تحلیل تکنیکال ممکن نیست: {analysis.get('message')}")
            return

        fund = get_fundamental(s, pick)
        score = compute_score(pick, analysis, fundamental=fund)

        ms = classify_market(s)
        neg_map = {n["symbol"]: n for n in negative_symbols_today(s, days=3)}

        signal = generate_signal(
            score,
            portfolio_value=portfolio_value,
            risk_per_trade_pct=risk_pct,
            min_rr=min_rr,
            market_state=ms,
            recent_negative_news=(pick in neg_map),
        )
    finally:
        s.close()

    # Tabs: chart+signal / order flow / news / score
    tab_main, tab_flow, tab_news, tab_score = st.tabs([
        "📈 نمودار و سیگنال",
        "📊 تابلوخوانی",
        "📰 اخبار",
        "🔬 امتیاز",
    ])

    with tab_main:
        chart = candlestick_with_setup(
            df, symbol=pick,
            entry=signal.entry_low,
            entry_high=signal.entry_high,
            stop=signal.stop_loss if signal.action == "BUY_CANDIDATE" else None,
            target1=signal.target1 if signal.action == "BUY_CANDIDATE" else None,
            target2=signal.target2 if signal.action == "BUY_CANDIDATE" else None,
            height=620,
        )
        if chart:
            st.plotly_chart(
                chart, use_container_width=True,
                config={"displayModeBar": True, "scrollZoom": True},
                key=f"chart_{pick}",
            )
        else:
            st.info("برای نمایش نمودار، حداقل ۲۰ کندل لازم است.")

        st.divider()
        render_signal_card(signal, score, df.tail(60))

    with tab_flow:
        _render_order_flow_tab(pick)

    with tab_news:
        s = get_session()
        try:
            news = recent_news(s, limit=15, symbol=pick)
        finally:
            s.close()

        if not news:
            st.info("خبری برای این نماد ثبت نشده.")
        else:
            for n in news[:10]:
                icon = ("🟢" if n.sentiment_score > 0.2 else
                        "🔴" if n.sentiment_score < -0.2 else "⚪")
                st.markdown(
                    f"{icon} `{n.published_at.strftime('%Y-%m-%d')}` "
                    f"**{n.title[:100]}** — {n.impact}"
                )

    with tab_score:
        _render_score_breakdown(score)
        fund = get_fundamental(get_session(), pick)
        if fund:
            st.divider()
            st.subheader("💼 بنیادی")
            f1, f2 = st.columns(2)
            f1.metric("P/E", fund.get("pe") or "-")
            f2.metric("ROE", f"{fund.get('roe')}%"
                     if fund.get("roe") is not None else "-")
            f3, f4 = st.columns(2)
            f3.metric("رشد سود", f"{fund.get('profit_growth')}%"
                     if fund.get("profit_growth") is not None else "-")
            f4.metric("نسبت بدهی", f"{fund.get('debt_ratio')}%"
                     if fund.get("debt_ratio") is not None else "-")


def _render_order_flow_tab(symbol: str) -> None:
    st.caption(
        "جریان پول حقیقی/حقوقی، قدرت خریدار، و عمق بازار. "
        "**داده از TSETMC** — اگر endpoint قطع باشد، بعداً امتحان کنید."
    )

    s = get_session()
    try:
        rpt = analyze_order_flow(s, symbol, days=30)
    finally:
        s.close()

    c1, c2 = st.columns([1, 3])
    with c1:
        if st.button("📥 دریافت order-flow", key=f"of_fetch_{symbol}"):
            with st.spinner("در حال دریافت..."):
                s2 = get_session()
                try:
                    from data_sources.tsetmc_orderflow import TsetmcOrderFlowAdapter
                    ok, msg = sync_order_flow(
                        s2, TsetmcOrderFlowAdapter(), symbol, days=30,
                    )
                finally:
                    s2.close()
            if ok:
                st.success(msg)
                st.rerun()
            else:
                st.error(msg)

    if not rpt.available:
        warning_banner(rpt.message, "info")
        for w in rpt.warnings:
            st.caption(w)
        return

    latest = rpt.latest
    st.divider()

    # KPI cards
    k1, k2, k3, k4 = st.columns(4)
    k1.metric(
        "قدرت خریدار", f"{latest.buy_power:.2f}×",
        help="نسبت حجم خرید کل به فروش کل. بالای ۱ = خریدار غالب",
    )
    k2.metric(
        "Smart Money", f"{latest.smart_money_score:.0f}",
        help="۰=حقوقی فروشنده قوی، ۵۰=خنثی، ۱۰۰=حقوقی خریدار قوی",
    )

    dominant_fa = {
        "inst": "🏛️ حقوقی", "retail": "👤 حقیقی", "balanced": "⚖️ متعادل",
    }.get(latest.dominant, latest.dominant)
    k3.metric("غالب", dominant_fa)

    trend_fa = {
        "improving": "📈 در حال بهبود",
        "deteriorating": "📉 در حال تضعیف",
        "stable": "↔ پایدار",
    }.get(rpt.trend_5d, "-")
    k4.metric("روند ۵ روزه", trend_fa)

    # Smart money score interpretation
    if latest.smart_money_score >= 65:
        warning_banner(
            f"🟢 حقوقی‌ها در {rpt.history[-1].ts.strftime('%Y-%m-%d')} "
            f"خریدار قابل توجهی بوده‌اند — نشانه مثبت.",
            "ok",
        )
    elif latest.smart_money_score <= 35:
        warning_banner(
            f"🔴 حقوقی‌ها در {rpt.history[-1].ts.strftime('%Y-%m-%d')} "
            f"فروشنده بوده‌اند — احتیاط.",
            "warn",
        )

    # Volumes table
    st.divider()
    st.subheader("جریان روزانه (سهام)")

    history_rows = [
        {
            "تاریخ": h.ts.strftime("%Y-%m-%d"),
            "خرید حقیقی": f"{h.buy_retail_vol:,.0f}",
            "خرید حقوقی": f"{h.buy_inst_vol:,.0f}",
            "فروش حقیقی": f"{h.sell_retail_vol:,.0f}",
            "فروش حقوقی": f"{h.sell_inst_vol:,.0f}",
            "قدرت خریدار": f"{h.buy_power:.2f}",
            "حقوقی خالص": f"{h.inst_net:+,.0f}",
            "SM Score": f"{h.smart_money_score:.0f}",
        }
        for h in rpt.history[-10:][::-1]
    ]
    st.dataframe(history_rows, width="stretch")

    # Order book (if available)
    if latest.buy_limit_price or latest.sell_limit_price:
        st.divider()
        st.subheader("عمق بازار (آخرین اسنپ‌شات)")
        st.caption(
            "فقط در ساعات بازار معنی دارد. اگر بعد از ساعت بازار fetch کردید، "
            "این اعداد ممکن است قدیمی باشند."
        )
        ob1, ob2 = st.columns(2)
        with ob1:
            st.metric("بهترین خرید (Bid)",
                     f"{latest.buy_limit_price or 0:,.0f}",
                     delta=f"حجم {latest.buy_limit_vol or 0:,.0f}")
        with ob2:
            st.metric("بهترین فروش (Ask)",
                     f"{latest.sell_limit_price or 0:,.0f}",
                     delta=f"حجم {latest.sell_limit_vol or 0:,.0f}")

    # Chart
    if len(rpt.history) >= 3:
        st.divider()
        st.subheader("نمودار قدرت خریدار (۳۰ روز)")
        _render_pressure_chart(rpt.history)


def _render_pressure_chart(history) -> None:
    import plotly.graph_objects as go

    dates = [h.ts for h in history]
    buy_powers = [h.buy_power for h in history]
    sm_scores = [h.smart_money_score for h in history]

    from plotly.subplots import make_subplots
    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        vertical_spacing=0.08,
        row_heights=[0.55, 0.45],
        subplot_titles=("قدرت خریدار", "Smart Money Score"),
    )

    fig.add_trace(go.Bar(
        x=dates, y=buy_powers,
        marker_color=[
            "#26a69a" if bp > 1 else "#ef5350" for bp in buy_powers
        ],
        name="قدرت خریدار",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.2f}×<extra></extra>",
    ), row=1, col=1)
    fig.add_hline(y=1.0, line=dict(dash="dot", color="#888"),
                  row=1, col=1)

    fig.add_trace(go.Scatter(
        x=dates, y=sm_scores,
        mode="lines+markers",
        line=dict(color="#42a5f5", width=2),
        marker=dict(size=5),
        name="SM Score",
        fill="tozeroy",
        fillcolor="rgba(66,165,245,0.15)",
        hovertemplate="%{x|%Y-%m-%d}<br>%{y:.0f}<extra></extra>",
    ), row=2, col=1)
    fig.add_hline(y=50, line=dict(dash="dash", color="#888"),
                  row=2, col=1)
    fig.update_yaxes(range=[0, 100], row=2, col=1)

    fig.update_layout(
        height=420,
        template="plotly_dark",
        showlegend=False,
        margin=dict(l=10, r=10, t=40, b=10),
        font=dict(family="Tahoma, Arial"),
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        hovermode="x unified",
    )
    st.plotly_chart(fig, use_container_width=True,
                    config={"displayModeBar": False},
                    key=f"pressure_{dates[-1]}")


def _render_score_breakdown(score) -> None:
    components = [
        ("تکنیکال", score.technical_score, "#26a69a"),
        ("مومنتوم", score.momentum_score, "#42a5f5"),
        ("نقدشوندگی", score.liquidity_score, "#ab47bc"),
        ("ریسک", score.risk_score, "#ffa726"),
        ("قدرت روند", score.trend_strength_score, "#26c6da"),
        ("بنیادی", score.fundamental_score, "#8d6e63"),
    ]

    for label, value, color in components:
        bar_html = (
            f"<div style='margin:8px 0;'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"font-size:12px;color:#ccc;margin-bottom:2px;'>"
            f"<span>{label}</span><span>{value:.0f}</span></div>"
            f"<div style='background:#222;height:8px;border-radius:4px;"
            f"overflow:hidden;'>"
            f"<div style='width:{value}%;height:100%;background:{color};'></div>"
            f"</div></div>"
        )
        st.markdown(bar_html, unsafe_allow_html=True)

    st.markdown(
        f"<div style='margin-top:12px;padding:8px;"
        f"background:rgba(255,255,255,0.05);border-radius:6px;"
        f"text-align:center;'><b>امتیاز کل (percentile): "
        f"<span style='font-size:22px;color:#26a69a;'>{score.total:.1f}</span>"
        f"</b></div>",
        unsafe_allow_html=True,
    )