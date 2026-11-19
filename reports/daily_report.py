"""
End-of-day report generator with news section.
"""

from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import (
    Instrument, PriceBar, DataSourceStatus, MarketSnapshot,
)
from portfolio.holdings import compute_holdings
from paper_trading.tracker import paper_stats
from alerts.engine import check_alerts
from analysis.industry import (
    IndustryStat, build_industry_map, aggregate_by_industry,
)
from news.query import negative_symbols_today, recent_news


@dataclass
class DailyReport:
    generated_at: datetime = field(default_factory=datetime.now)
    trade_date: str = ""
    market_state: str = "unknown"
    market_score: float = 0.0
    breadth_today: float = 0.0
    trend_breadth: float = 0.0
    vol_ratio: float = 1.0
    n_active_instruments: int = 0
    n_priced_instruments: int = 0
    n_data_failures: int = 0
    last_sync_time: str = "-"
    open_paper_signals: int = 0
    paper_total_closed: int = 0
    paper_win_rate: float = 0.0
    paper_avg_r: float = 0.0
    paper_profit_factor: float = 0.0
    portfolio_value: float = 0.0
    portfolio_cost: float = 0.0
    portfolio_pl: float = 0.0
    portfolio_pl_pct: float = 0.0
    n_positions: int = 0
    critical_alerts: int = 0
    warning_alerts: int = 0
    info_alerts: int = 0
    alert_lines: list[str] = field(default_factory=list)
    eq_index: float = 0.0
    eq_index_pct: float = 0.0
    top_symbols: list[dict] = field(default_factory=list)
    industries: list[IndustryStat] = field(default_factory=list)
    # News section
    news_negative: list[dict] = field(default_factory=list)
    news_high_impact: list[dict] = field(default_factory=list)
    news_total_last3d: int = 0
    recommendations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    rankings_provided: bool = False


def build_daily_report(
    session: Session,
    rankings: list | None = None,
) -> DailyReport:
    rpt = DailyReport()
    now = datetime.now()
    rpt.trade_date = now.strftime("%Y-%m-%d")
    rpt.generated_at = now

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    snap = session.scalar(
        select(MarketSnapshot).where(MarketSnapshot.trade_date == today).limit(1)
    )
    if snap is None:
        snap = session.scalar(
            select(MarketSnapshot).order_by(MarketSnapshot.trade_date.desc()).limit(1)
        )
    if snap:
        rpt.market_state = snap.state
        rpt.market_score = snap.state_score
        rpt.breadth_today = snap.breadth_today
        rpt.trend_breadth = snap.trend_breadth
        rpt.vol_ratio = snap.vol_ratio
        rpt.eq_index = snap.eq_index
        rpt.eq_index_pct = snap.eq_index_pct
    else:
        rpt.warnings.append("هیچ snapshot بازار ثبت نشده.")

    rpt.n_active_instruments = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    rpt.n_priced_instruments = session.scalar(
        select(func.count(func.distinct(PriceBar.instrument_id)))
    ) or 0
    rpt.n_data_failures = max(0, rpt.n_active_instruments - rpt.n_priced_instruments)

    last_success = session.scalar(select(func.max(DataSourceStatus.last_success_at)))
    if last_success:
        rpt.last_sync_time = last_success.strftime("%Y-%m-%d %H:%M")
        if (now - last_success).total_seconds() > 24 * 3600:
            rpt.warnings.append(f"آخرین سینک > ۲۴ ساعت پیش ({rpt.last_sync_time})")

    ps = paper_stats(session)
    rpt.open_paper_signals = ps["open"]
    rpt.paper_total_closed = ps["total"]
    rpt.paper_win_rate = ps["win_rate"]
    rpt.paper_avg_r = ps["avg_r"]
    rpt.paper_profit_factor = ps["profit_factor"]

    holdings = compute_holdings(session)
    if holdings:
        rpt.n_positions = len(holdings)
        rpt.portfolio_value = sum(h.market_value for h in holdings)
        rpt.portfolio_cost = sum(h.cost_basis for h in holdings)
        rpt.portfolio_pl = rpt.portfolio_value - rpt.portfolio_cost
        rpt.portfolio_pl_pct = (
            rpt.portfolio_pl / rpt.portfolio_cost * 100.0
            if rpt.portfolio_cost > 0 else 0.0
        )

    alerts = check_alerts(session)
    for a in alerts:
        if a.level == "critical":
            rpt.critical_alerts += 1
        elif a.level == "warning":
            rpt.warning_alerts += 1
        else:
            rpt.info_alerts += 1
        rpt.alert_lines.append(f"[{a.level}] {a.message}")

    # ---- Rankings ----
    if rankings:
        rpt.rankings_provided = True
        sorted_rank = sorted(rankings, key=lambda x: -x.total)
        for r in sorted_rank[:10]:
            tech = r.technical or {}
            rpt.top_symbols.append({
                "symbol": r.symbol,
                "score": round(r.total, 1),
                "trend": tech.get("trend", "-"),
                "rsi": round(tech.get("rsi14") or 0, 1),
                "close": round(tech.get("last_close") or 0, 0),
                "reasons": list(r.reasons[:2]) if r.reasons else [],
                "red_flags": list(r.red_flags[:2]) if r.red_flags else [],
            })
        ind_map = build_industry_map(session)
        rpt.industries = aggregate_by_industry(rankings, ind_map)

    # ---- News ----
    rpt.news_negative = negative_symbols_today(session, days=3)
    recent = recent_news(session, limit=200)
    from datetime import timedelta
    cutoff = now - timedelta(days=3)
    rpt.news_total_last3d = sum(
        1 for n in recent if n.published_at >= cutoff
    )
    rpt.news_high_impact = [
        {
            "published_at": n.published_at.strftime("%Y-%m-%d %H:%M"),
            "title": n.title,
            "sentiment": n.sentiment_score,
            "impact": n.impact,
            "category": n.category,
            "source": n.source,
        }
        for n in recent
        if n.impact == "high" and n.published_at >= cutoff
    ][:10]

    # ---- Recommendations ----
    if rpt.market_state == "red":
        rpt.recommendations.append(
            "بازار قرمز: حجم پوزیشن‌های جدید را نصف کنید. نقد ماندن معتبر است."
        )
    elif rpt.market_state == "green":
        rpt.recommendations.append(
            "بازار سبز: شرایط برای ورود با حجم عادی مساعد است."
        )
    else:
        rpt.recommendations.append(
            "بازار زرد: با احتیاط عمل کنید؛ آستانه امتیاز بالاتر است."
        )

    if rpt.n_data_failures > 0:
        rpt.recommendations.append(
            f"{rpt.n_data_failures} نماد داده ندارند — از صفحه «داده» re-sync."
        )

    if rpt.critical_alerts > 0:
        rpt.recommendations.append(
            f"{rpt.critical_alerts} هشدار بحرانی فعال است."
        )

    if rpt.news_negative:
        top3 = ", ".join(
            f"{n['symbol']}({n['worst_sentiment']:.1f})"
            for n in rpt.news_negative[:3]
        )
        rpt.recommendations.append(
            f"خبر منفی اخیر برای {len(rpt.news_negative)} نماد — "
            f"این نمادها از سیگنال خرید حذف می‌شوند: {top3}"
        )

    if rpt.paper_total_closed >= 20:
        if rpt.paper_avg_r > 0.2 and rpt.paper_win_rate > 35:
            rpt.recommendations.append(
                "Paper Trading edge دارد — آماده بررسی برای ورود واقعی."
            )
        elif rpt.paper_avg_r > 0:
            rpt.recommendations.append(
                "Paper Trading مثبت ولی ضعیف — صبر کنید."
            )
        else:
            rpt.recommendations.append(
                "Paper Trading منفی است — استراتژی را بازبینی کنید."
            )
    else:
        rpt.recommendations.append(
            f"فقط {rpt.paper_total_closed} معامله Paper بسته — حداقل ۲۰ لازم است."
        )

    if rpt.industries:
        strong = rpt.industries[0]
        if strong.avg_score >= 65:
            rpt.recommendations.append(
                f"صنعت قوی: «{strong.industry}» میانگین {strong.avg_score} "
                f"({strong.n_symbols} نماد)"
            )

    return rpt


def to_markdown(rpt: DailyReport) -> str:
    lines = [
        f"# گزارش روزانه — {rpt.trade_date}",
        f"_تولید شده در {rpt.generated_at.strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## ۱. وضعیت بازار",
        f"- وضعیت: **{rpt.market_state}** (امتیاز {rpt.market_score}/100)",
        f"- عرض امروز: {rpt.breadth_today}%",
        f"- بالای EMA50: {rpt.trend_breadth}%",
        f"- نسبت حجم: {rpt.vol_ratio}×",
        f"- شاخص هم‌وزن: {rpt.eq_index:.2f} ({rpt.eq_index_pct:+.2f}%)",
        "",
        "## ۲. سلامت داده",
        f"- فعال: {rpt.n_active_instruments} | دارای قیمت: {rpt.n_priced_instruments} | بدون داده: {rpt.n_data_failures}",
        f"- آخرین سینک: {rpt.last_sync_time}",
        "",
        "## ۳. اخبار",
        f"- اخبار ۳ روز اخیر: {rpt.news_total_last3d}",
        f"- نمادهای با خبر منفی: {len(rpt.news_negative)}",
    ]
    if rpt.news_negative:
        for n in rpt.news_negative[:10]:
            lines.append(
                f"  - {n['symbol']} — احساس {n['worst_sentiment']:.2f} — "
                f"{n['count']} خبر — {n['latest_title'][:80]}"
            )

    lines.extend([
        "",
        "## ۴. Paper Trading",
        f"- باز: {rpt.open_paper_signals} | بسته: {rpt.paper_total_closed}",
        f"- نرخ برد: {rpt.paper_win_rate}% | میانگین R: {rpt.paper_avg_r:.3f} | PF: {rpt.paper_profit_factor:.2f}",
        "",
        "## ۵. پرتفوی",
        f"- موقعیت‌ها: {rpt.n_positions}",
        f"- ارزش: {rpt.portfolio_value:,.0f} ریال | بهای تمام: {rpt.portfolio_cost:,.0f} | "
        f"سود/زیان: {rpt.portfolio_pl:,.0f} ({rpt.portfolio_pl_pct:+.2f}%)",
        "",
        "## ۶. هشدارها",
        f"- بحرانی: {rpt.critical_alerts} | اخطار: {rpt.warning_alerts} | اطلاع: {rpt.info_alerts}",
    ])
    if rpt.alert_lines:
        for a in rpt.alert_lines[:10]:
            lines.append(f"  - {a}")

    if rpt.top_symbols:
        lines.append("")
        lines.append("## ۷. ۱۰ نماد برتر")
        lines.append("")
        lines.append("| # | نماد | امتیاز | روند | RSI | قیمت |")
        lines.append("|---|---|---:|---|---:|---:|")
        trend_map = {"up": "📈", "down": "📉", "neutral": "↔"}
        for i, t in enumerate(rpt.top_symbols, 1):
            lines.append(
                f"| {i} | {t['symbol']} | {t['score']} | "
                f"{trend_map.get(t['trend'], '-')} | {t['rsi']} | {t['close']:,.0f} |"
            )

    if rpt.industries:
        lines.append("")
        lines.append("## ۸. صنایع")
        lines.append("")
        lines.append("| صنعت | تعداد | امتیاز | RSI | روند صعودی |")
        lines.append("|---|---:|---:|---:|---:|")
        for s in rpt.industries[:10]:
            lines.append(
                f"| {s.industry} | {s.n_symbols} | {s.avg_score} | "
                f"{s.avg_rsi} | {s.pct_uptrend}% |"
            )

    if rpt.recommendations:
        lines.append("")
        lines.append("## ۹. توصیه‌های سیستم")
        for r in rpt.recommendations:
            lines.append(f"- {r}")

    if rpt.warnings:
        lines.append("")
        lines.append("## ⚠️ هشدارها")
        for w in rpt.warnings:
            lines.append(f"- {w}")

    if not rpt.rankings_provided:
        lines.append("")
        lines.append("_رتبه‌بندی ارائه نشده — بخش‌های ۷ و ۸ خالی._")

    return "\n".join(lines)