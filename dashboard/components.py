"""
Reusable UI components (signal cards, checklists, pills, banners,
global alerts).
"""

import streamlit as st

from dashboard.chart_helpers import sparkline


def status_pill(text: str, status: str = "info") -> str:
    """Return HTML for a colored pill."""
    color = {
        "ok": "#26a69a",
        "warn": "#ffc107",
        "bad": "#ef5350",
        "info": "#2196f3",
    }.get(status, "#666")
    text_color = "black" if status == "warn" else "white"
    return (
        f"<span style='background:{color};color:{text_color};"
        f"padding:3px 10px;border-radius:12px;font-size:12px;"
        f"font-weight:600;'>{text}</span>"
    )


def _action_label(action: str) -> str:
    return {
        "BUY_CANDIDATE": "🟢 قابل خرید",
        "WATCH": "🟡 پیگیری",
        "AVOID": "🔴 رد",
    }.get(action, action)


def _action_color(action: str) -> str:
    return {
        "BUY_CANDIDATE": "#26a69a",
        "WATCH": "#ffc107",
        "AVOID": "#ef5350",
    }.get(action, "#666")


def _build_checklist(tech: dict, signal) -> tuple[list[str], list[str]]:
    """Return (passed, failed) short labels for visual checklist."""
    passed, failed = [], []
    if not tech or not tech.get("ok"):
        return passed, failed

    trend = tech.get("trend", "neutral")
    if trend == "up":
        passed.append("روند صعودی")
    elif trend == "down":
        failed.append("روند نزولی")
    else:
        failed.append("روند خنثی")

    rsi = tech.get("rsi14") or 50
    if 40 <= rsi <= 65:
        passed.append(f"RSI سالم ({rsi:.0f})")
    elif rsi > 75:
        failed.append(f"RSI اشباع خرید ({rsi:.0f})")
    elif rsi < 30:
        passed.append(f"RSI اشباع فروش ({rsi:.0f})")
    else:
        passed.append(f"RSI ({rsi:.0f})")

    adx = tech.get("adx14") or 0
    if adx >= 20:
        passed.append(f"ADX قوی ({adx:.0f})")
    else:
        failed.append(f"ADX ضعیف ({adx:.0f})")

    atr_pct = tech.get("atr_pct") or 0
    if atr_pct >= 0.5:
        passed.append(f"نوسان کافی ({atr_pct:.2f}%)")
    else:
        failed.append(f"نوسان کم ({atr_pct:.2f}%)")

    rr = getattr(signal, "rr_ratio", None)
    if rr and rr >= 2:
        passed.append(f"R/R خوب ({rr:.1f})")
    elif rr:
        failed.append(f"R/R کم ({rr:.1f})")

    return passed, failed


def _render_checklist(passed: list[str], failed: list[str]) -> None:
    if not passed and not failed:
        return
    st.markdown("**چک‌لیست معیارها:**")
    lines = []
    for p in passed[:6]:
        lines.append(
            f"<div style='color:#26a69a;font-size:13px;'>✅ {p}</div>"
        )
    for f in failed[:4]:
        lines.append(
            f"<div style='color:#ef5350;font-size:13px;'>❌ {f}</div>"
        )
    st.markdown(
        "<div style='line-height:1.7;'>" + "".join(lines) + "</div>",
        unsafe_allow_html=True,
    )


def render_signal_card(signal, score_breakdown, df=None) -> None:
    """
    Visual signal card with setup grid, sparkline, and inline checklist pills.
    Colors use rgba so both light and dark themes look good.
    """
    action = signal.action
    color = _action_color(action)
    last_close = signal.last_close or 0

    # Compute change % if df available
    change_pct = None
    spark_fig = None
    if df is not None and len(df) >= 2:
        first = float(df["close"].iloc[0])
        if first > 0:
            change_pct = (float(df["close"].iloc[-1]) - first) / first * 100
        spark_fig = sparkline(df.tail(60), height=60)

    with st.container(border=True):
        # ---- Header: symbol + score + action badge ----
        hc1, hc2, hc3 = st.columns([3, 1, 1])
        with hc1:
            st.markdown(f"### {signal.symbol}")
        with hc2:
            st.markdown(
                f"<div style='text-align:center;padding:4px 0;'>"
                f"<div style='font-size:10px;color:var(--text-muted);'>امتیاز</div>"
                f"<div style='font-size:22px;font-weight:700;color:var(--text-primary);'>"
                f"{signal.score:.0f}</div></div>",
                unsafe_allow_html=True,
            )
        with hc3:
            st.markdown(
                f"<div style='background:{color};color:white;"
                f"padding:8px 4px;border-radius:8px;text-align:center;"
                f"font-weight:700;font-size:12px;margin-top:6px;'>"
                f"{_action_label(action)}</div>",
                unsafe_allow_html=True,
            )

        # ---- Price row ----
        if change_pct is not None:
            chg_color = "#26a69a" if change_pct >= 0 else "#ef5350"
            st.markdown(
                f"<div style='display:flex;align-items:baseline;gap:10px;"
                f"margin:6px 0;'>"
                f"<span style='font-size:20px;font-weight:700;"
                f"color:var(--text-primary);'>{last_close:,.0f}</span>"
                f"<span style='color:{chg_color};font-size:12px;'>"
                f"{change_pct:+.2f}% (۶۰ روز)</span></div>",
                unsafe_allow_html=True,
            )

        # ---- Sparkline ----
        if spark_fig is not None:
            st.plotly_chart(
                spark_fig, use_container_width=True,
                config={"displayModeBar": False},
                key=f"spark_{signal.symbol}_{action}",
            )

        # ---- Setup grid (only for BUY) ----
        if action == "BUY_CANDIDATE":
            _render_setup_grid(signal)
            _render_metrics_row(signal)

        # ---- Checklist pills ----
        tech = score_breakdown.technical if score_breakdown else {}
        passed, failed = _build_checklist(tech, signal)
        _render_checklist_pills(passed, failed)

        # ---- Action buttons ----
        b1, b2 = st.columns(2)
        with b1:
            if st.button(
                "🔍 تحلیل کامل",
                key=f"detail_{signal.symbol}_{action}",
                use_container_width=True,
            ):
                st.session_state["_pending_page"] = "🔍 تحلیل نماد"
                st.session_state["_pending_symbol"] = signal.symbol
                st.rerun()
        with b2:
            if st.button(
                "📝 Paper",
                key=f"paper_{signal.symbol}_{action}",
                use_container_width=True,
            ):
                st.session_state["_pending_page"] = "📝 Paper Trading"
                st.rerun()

        # ---- Expandable details ----
        with st.expander("📋 دلایل و شروط ابطال"):
            if signal.reasons:
                st.markdown("**دلایل ورود:**")
                for r in signal.reasons:
                    st.markdown(f"- {r}")
            if signal.invalidations:
                st.markdown("**شروط ابطال:**")
                for x in signal.invalidations:
                    st.markdown(f"- {x}")


# ---- New helpers for signal card ----

def _render_setup_grid(signal):
    """4-cell visual grid: stop / entry / target1 / target2."""
    last = signal.last_close or 0
    cells = [
        ("🛑 حد ضرر", signal.stop_loss, "#ef5350"),
        ("➡️ ورود", (signal.entry_low, signal.entry_high), "#40c4ff"),
        ("🎯 هدف ۱", signal.target1, "#26a69a"),
        ("🎯 هدف ۲", signal.target2, "#008e73"),
    ]
    cols = st.columns(4)
    for col, (label, val, c) in zip(cols, cells):
        with col:
            if isinstance(val, tuple):
                v1, v2 = val
                display = f"{v1:,.0f}–{v2:,.0f}"
                pct_html = ""
            else:
                display = f"{val:,.0f}" if val else "-"
                if val and last > 0:
                    pct = (val - last) / last * 100
                    pct_c = "#26a69a" if pct >= 0 else "#ef5350"
                    pct_html = (
                        f"<div style='font-size:10px;color:{pct_c};'>"
                        f"{pct:+.1f}%</div>"
                    )
                else:
                    pct_html = ""
            col.markdown(
                f"<div style='background:rgba(128,128,128,0.06);"
                f"border:1px solid {c};border-radius:8px;"
                f"padding:6px 3px;text-align:center;'>"
                f"<div style='font-size:9px;color:var(--text-muted);'>{label}</div>"
                f"<div style='font-size:12px;font-weight:700;"
                f"color:var(--text-primary);'>{display}</div>"
                f"{pct_html}</div>",
                unsafe_allow_html=True,
            )


def _render_metrics_row(signal):
    cols = st.columns(3)
    metrics = [
        ("R/R", f"{signal.rr_ratio:.2f}" if signal.rr_ratio else "-"),
        ("حجم", f"{signal.position_size:,}"),
        ("ریسک", f"{signal.risk_amount:,.0f}"),
    ]
    for col, (label, value) in zip(cols, metrics):
        col.metric(label, value)


def _render_checklist_pills(passed, failed):
    """Compact inline pills for pass/fail checks."""
    if not passed and not failed:
        return
    html = "<div style='display:flex;flex-wrap:wrap;gap:4px;margin:10px 0 6px 0;'>"
    for p in passed[:5]:
        html += (
            f"<span style='background:rgba(38,166,154,0.15);"
            f"color:#26a69a;padding:3px 9px;border-radius:10px;"
            f"font-size:11px;font-weight:600;'>✅ {p}</span>"
        )
    for f in failed[:3]:
        html += (
            f"<span style='background:rgba(239,83,80,0.15);"
            f"color:#ef5350;padding:3px 9px;border-radius:10px;"
            f"font-size:11px;font-weight:600;'>❌ {f}</span>"
        )
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

def warning_banner(text: str, level: str = "warn") -> None:
    color = {
        "info": "#2196f3",
        "warn": "#ffc107",
        "bad": "#ef5350",
        "ok": "#26a69a",
    }.get(level, "#888")
    icon = {
        "info": "ℹ️", "warn": "⚠️", "bad": "🔴", "ok": "✅",
    }.get(level, "•")
    st.markdown(
        f"<div style='background:rgba(255,255,255,0.05);"
        f"border-right:4px solid {color};padding:10px;"
        f"border-radius:6px;margin:8px 0;'>{icon} {text}</div>",
        unsafe_allow_html=True,
    )


def render_global_alert_banner() -> None:
    """Compact alert banner shown on every page when alerts exist."""
    try:
        from database.session import get_session
        from alerts.engine import check_alerts

        s = get_session()
        try:
            alerts = check_alerts(s)
        finally:
            s.close()

        critical = [a for a in alerts if a.level == "critical"]
        warning = [a for a in alerts if a.level == "warning"]

        if not critical and not warning:
            return

        if critical:
            color = "#ef5350"
            icon = "🔴"
            top = critical[0].message
            extra = (f" + {len(critical) - 1} بحرانی دیگر"
                     if len(critical) > 1 else "")
        else:
            color = "#ffc107"
            icon = "🟠"
            top = warning[0].message
            extra = (f" + {len(warning) - 1} اخطار دیگر"
                     if len(warning) > 1 else "")

        st.markdown(
            f"<div style='background:rgba(255,255,255,0.04);"
            f"border-right:4px solid {color};"
            f"padding:8px 14px;border-radius:6px;"
            f"margin:6px 0 12px 0;font-size:13px;'>"
            f"{icon} <b>{top[:150]}</b>"
            f"<span style='color:#888;margin-right:8px;'>{extra}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
    except Exception:
        pass


# ===========================================================================
# Visual signals tab
# ===========================================================================

def render_visual_signals_tab(
    portfolio_value: float,
    risk_pct: float,
    min_rr: float,
) -> None:
    """Full visual signals view: cards + heatmap. Called from signals page."""
    from database.session import get_session
    from decision_engine.signal_generator import generate_signal
    from news.query import negative_symbols_today
    from dashboard.chart_helpers import market_heatmap

    rankings = st.session_state.get("rankings", [])
    ms = st.session_state.get("market_state")

    if not rankings:
        warning_banner(
            "هنوز رتبه‌بندی محاسبه نشده. به صفحه «📈 قیمت و تحلیل» بروید "
            "و دکمه محاسبه را بزنید. سپس اینجا کارت‌های بصری را ببینید.",
            "info",
        )
        return

    s = get_session()
    try:
        neg_map = {n["symbol"]: n for n in negative_symbols_today(s, days=3)}

        signals_with_score = []
        for r in rankings[:20]:
            sig = generate_signal(
                r,
                portfolio_value=portfolio_value,
                risk_per_trade_pct=risk_pct,
                min_rr=min_rr,
                market_state=ms,
                recent_negative_news=(r.symbol in neg_map),
            )
            signals_with_score.append((sig, r))

        buys = [(sig, r) for sig, r in signals_with_score
                if sig.action == "BUY_CANDIDATE"]

        if ms:
            state_label = {
                "green": "🟢 بازار سبز",
                "yellow": "🟡 بازار زرد",
                "red": "🔴 بازار قرمز",
            }.get(ms.state, ms.state)
            warning_banner(
                f"{state_label} — ضریب حجم {ms.size_multiplier}× | "
                f"افزایش آستانه +{ms.score_bonus:.0f}",
                "ok" if ms.state == "green" else
                "bad" if ms.state == "red" else "warn",
            )

        if not buys:
            warning_banner(
                f"هیچ‌کدام از ۲۰ نماد برتر شرایط خرید کامل ندارند. "
                f"در بازار {ms.state if ms else 'نامشخص'} این طبیعی است. "
                f"نقد ماندن یک تصمیم معتبر است.",
                "warn",
            )
        else:
            st.subheader(f"🟢 {len(buys)} سیگنال خرید معتبر")
            for i in range(0, len(buys), 2):
                pair = buys[i:i + 2]
                cols = st.columns(len(pair))
                for col, (sig, score_bd) in zip(cols, pair):
                    with col:
                        df = _load_symbol_df(s, sig.symbol, bars=60)
                        render_signal_card(sig, score_bd, df)

        watches = [(sig, r) for sig, r in signals_with_score
                   if sig.action == "WATCH"]
        avoids = [(sig, r) for sig, r in signals_with_score
                  if sig.action == "AVOID"]

        st.divider()
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**🟡 پیگیری ({len(watches)})**")
            if watches:
                st.dataframe(
                    [{"نماد": sig.symbol, "امتیاز": round(sig.score, 1),
                      "دلیل": "; ".join(sig.invalidations[:2])}
                     for sig, _ in watches],
                    width="stretch",
                    height=min(300, 35 * len(watches) + 40),
                )
        with c2:
            st.markdown(f"**🔴 رد ({len(avoids)})**")
            if avoids:
                st.dataframe(
                    [{"نماد": sig.symbol, "امتیاز": round(sig.score, 1),
                      "دلیل": "; ".join(sig.invalidations[:2])}
                     for sig, _ in avoids],
                    width="stretch",
                    height=min(300, 35 * len(avoids) + 40),
                )

        st.divider()
        st.subheader("🗺️ نقشه بازار")
        st.caption(
            "هر خانه یک نماد — اندازه بر اساس نوسان، رنگ بر اساس درصد تغییر."
        )
        heatmap_data = _build_heatmap_data(s, rankings[:100])
        if heatmap_data:
            fig = market_heatmap(heatmap_data)
            if fig:
                st.plotly_chart(
                    fig, use_container_width=True,
                    config={"displayModeBar": False},
                    key="market_heatmap",
                )
    finally:
        s.close()


def _load_symbol_df(session, symbol: str, bars: int = 60):
    import pandas as pd
    from sqlalchemy import select
    from database.models import Instrument, PriceBar

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


def _build_heatmap_data(session, rankings_top: list) -> list[dict]:
    from sqlalchemy import select
    from database.models import Instrument, PriceBar

    out = []
    for r in rankings_top:
        inst = session.scalar(
            select(Instrument).where(Instrument.symbol == r.symbol)
        )
        if inst is None:
            continue
        last_two = (
            session.query(PriceBar)
            .filter(PriceBar.instrument_id == inst.id)
            .order_by(PriceBar.ts.desc())
            .limit(2)
            .all()
        )
        if len(last_two) < 2 or last_two[1].close <= 0:
            continue
        pct = (last_two[0].close - last_two[1].close) / last_two[1].close * 100
        out.append({
            "symbol": r.symbol,
            "pct_change": round(pct, 2),
            "score": r.total,
            "close": last_two[0].close,
        })
    return out


def render_industry_treemap_tab() -> None:
    """Treemap view of industries. Called from industry page as a new tab."""
    from database.session import get_session
    from analysis.industry import build_industry_map, aggregate_by_industry
    from dashboard.chart_helpers import industry_treemap, market_breadth_bar

    rankings = st.session_state.get("rankings", [])
    if not rankings:
        warning_banner(
            "ابتدا در صفحه «📈 قیمت و تحلیل» رتبه‌بندی را محاسبه کنید.",
            "info",
        )
        return

    s = get_session()
    try:
        ind_map = build_industry_map(s)
        stats = aggregate_by_industry(rankings, ind_map)
    finally:
        s.close()

    if not stats:
        st.info("هیچ صنعتی شناسایی نشد.")
        return

    st.caption(
        f"{len(stats)} صنعت — **مساحت** = تعداد نماد، "
        f"**رنگ** = میانگین امتیاز."
    )

    fig = industry_treemap(stats, height=520)
    if fig:
        st.plotly_chart(
            fig, use_container_width=True,
            config={"displayModeBar": False},
            key="industry_treemap",
        )

    st.divider()
    st.subheader("تفکیک بازار")
    breadths = {}
    for stt in stats:
        if stt.industry.startswith("[") and stt.industry.endswith("]"):
            key = stt.industry.strip("[]")
            breadths[key] = {
                "avg_score": stt.avg_score,
                "n_symbols": stt.n_symbols,
            }
    if breadths:
        bar = market_breadth_bar(breadths, height=240)
        if bar:
            st.plotly_chart(
                bar, use_container_width=True,
                config={"displayModeBar": False},
                key="market_breadth_bar",
            )
def render_stepper(steps: list[dict]) -> None:
    """
    Visual stepper. steps = [{"n": 1, "label": "...", "status": "done|current|pending"}]
    """
    color_map = {
        "done": ("#26a69a", "#0d2e2a", "✓"),
        "current": ("#ffc107", "#2e2612", "●"),
        "pending": ("#555", "#1a1a1a", "○"),
    }
    parts = []
    for s in steps:
        border_color, bg_color, icon = color_map[s["status"]]
        parts.append(
            f"<div style='flex:1;display:flex;flex-direction:column;"
            f"align-items:center;padding:14px 8px;"
            f"background:{bg_color};border:2px solid {border_color};"
            f"border-radius:10px;margin:0 4px;'>"
            f"<div style='font-size:22px;color:{border_color};'>{icon}</div>"
            f"<div style='font-size:12px;color:#ccc;margin-top:2px;'>"
            f"گام {s['n']}</div>"
            f"<div style='font-size:13px;font-weight:600;color:#fff;'>"
            f"{s['label']}</div>"
            f"</div>"
        )
    st.markdown(
        f"<div style='display:flex;margin:16px 0;'>{''.join(parts)}</div>",
        unsafe_allow_html=True,
    )
def render_page_intro(page_name: str) -> None:
    """Show a compact intro banner at the top of a page."""
    from dashboard.help_content import PAGE_HELP
    help_info = PAGE_HELP.get(page_name)
    if not help_info:
        return

    with st.expander(f"ℹ️ راهنمای این صفحه — {help_info['title']}"):
        st.markdown(f"**چه کاری اینجا انجام می‌شود؟** {help_info['what']}")
        st.markdown(f"**چه زمانی؟** {help_info['when']}")
        st.markdown("**گام‌های صفحه:**")
        for step in help_info["steps"]:
            st.markdown(f"- {step}")