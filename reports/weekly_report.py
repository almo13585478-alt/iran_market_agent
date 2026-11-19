"""
Weekly report generator + file writer.

Covers last N days (default 7). Writes Markdown to data/reports/.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from app.config import BASE_DIR
from database.models import (
    Instrument, PaperSignal, MarketSnapshot, NewsItem, PortfolioTransaction,
)
from portfolio.holdings import compute_holdings


REPORT_DIR = BASE_DIR / "data" / "reports"


@dataclass
class WeeklyReport:
    week_start: datetime = field(default_factory=lambda: datetime.now() - timedelta(days=7))
    week_end: datetime = field(default_factory=datetime.now)
    generated_at: datetime = field(default_factory=datetime.now)
    # Paper
    paper_opened: int = 0
    paper_closed: int = 0
    paper_wins: int = 0
    paper_losses: int = 0
    paper_win_rate: float = 0.0
    paper_avg_r: float = 0.0
    paper_total_r: float = 0.0
    paper_profit_factor: float = 0.0
    best_trades: list[dict] = field(default_factory=list)
    worst_trades: list[dict] = field(default_factory=list)
    # Market state days
    green_days: int = 0
    yellow_days: int = 0
    red_days: int = 0
    # Benchmark
    eq_index_start: float | None = None
    eq_index_end: float | None = None
    eq_index_change_pct: float | None = None
    # Portfolio
    portfolio_value: float = 0.0
    portfolio_pl_pct: float = 0.0
    n_positions: int = 0
    # News
    news_count: int = 0
    news_negative_count: int = 0
    # Text
    recommendations: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def build_weekly_report(
    session: Session, days: int = 7,
) -> WeeklyReport:
    now = datetime.now()
    start = now - timedelta(days=days)
    rpt = WeeklyReport(week_start=start, week_end=now, generated_at=now)

    # ---- Paper signals ----
    opened = session.scalars(
        select(PaperSignal)
        .where(PaperSignal.signal_ts >= start)
    ).all()
    rpt.paper_opened = len(opened)

    closed = session.scalars(
        select(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
        .where(PaperSignal.exit_ts.isnot(None))
        .where(PaperSignal.exit_ts >= start)
    ).all()
    rpt.paper_closed = len(closed)
    if closed:
        wins = [s for s in closed if (s.r_multiple or 0) > 0]
        losses = [s for s in closed if (s.r_multiple or 0) <= 0]
        rpt.paper_wins = len(wins)
        rpt.paper_losses = len(losses)
        rpt.paper_win_rate = round(len(wins) / len(closed) * 100.0, 1)
        rpt.paper_total_r = round(sum(s.r_multiple or 0 for s in closed), 2)
        rpt.paper_avg_r = round(rpt.paper_total_r / len(closed), 3)
        gw = sum(s.r_multiple for s in wins)
        gl = abs(sum(s.r_multiple for s in losses))
        rpt.paper_profit_factor = round(gw / gl, 2) if gl > 0 else 99.99

        sorted_by_r = sorted(closed, key=lambda s: -(s.r_multiple or 0))
        rpt.best_trades = [
            {"symbol": s.symbol, "r": s.r_multiple,
             "strategy": s.strategy,
             "exit_ts": s.exit_ts.strftime("%Y-%m-%d")}
            for s in sorted_by_r[:5]
        ]
        rpt.worst_trades = [
            {"symbol": s.symbol, "r": s.r_multiple,
             "strategy": s.strategy,
             "exit_ts": s.exit_ts.strftime("%Y-%m-%d")}
            for s in sorted_by_r[-5:][::-1]
        ]

    # ---- Market state ----
    snaps = session.scalars(
        select(MarketSnapshot)
        .where(MarketSnapshot.trade_date >= start)
        .order_by(MarketSnapshot.trade_date)
    ).all()
    for s in snaps:
        if s.state == "green":
            rpt.green_days += 1
        elif s.state == "red":
            rpt.red_days += 1
        else:
            rpt.yellow_days += 1

    if snaps:
        rpt.eq_index_start = snaps[0].eq_index
        rpt.eq_index_end = snaps[-1].eq_index
        if rpt.eq_index_start > 0:
            rpt.eq_index_change_pct = round(
                (rpt.eq_index_end - rpt.eq_index_start) / rpt.eq_index_start * 100, 2
            )

    # ---- Portfolio ----
    holdings = compute_holdings(session)
    if holdings:
        rpt.n_positions = len(holdings)
        tv = sum(h.market_value for h in holdings)
        tc = sum(h.cost_basis for h in holdings)
        rpt.portfolio_value = tv
        rpt.portfolio_pl_pct = (
            round((tv - tc) / tc * 100, 2) if tc > 0 else 0.0
        )

    # ---- News ----
    news = session.scalars(
        select(NewsItem).where(NewsItem.published_at >= start)
    ).all()
    rpt.news_count = len(news)
    rpt.news_negative_count = sum(
        1 for n in news if (n.sentiment_score or 0) < -0.2
    )

    # ---- Recommendations ----
    if rpt.paper_closed >= 5:
        if rpt.paper_avg_r > 0.2 and rpt.paper_win_rate > 35:
            rpt.recommendations.append(
                f"هفته خوب: {rpt.paper_closed} معامله، avgR={rpt.paper_avg_r:.2f}."
            )
        elif rpt.paper_avg_r > 0:
            rpt.recommendations.append(
                f"هفته ضعیف: {rpt.paper_closed} معامله، avgR={rpt.paper_avg_r:.2f}."
            )
        else:
            rpt.recommendations.append(
                f"هفته منفی: avgR={rpt.paper_avg_r:.2f}. استراتژی را بازبینی کنید."
            )
    else:
        rpt.recommendations.append(
            f"فقط {rpt.paper_closed} معامله بسته — نمونه کوچک است."
        )

    if rpt.red_days >= 3:
        rpt.warnings.append(f"{rpt.red_days} روز قرمز در هفته — بازار پرنوسان بود.")

    if rpt.eq_index_change_pct is not None and rpt.eq_index_change_pct < -3:
        rpt.warnings.append(
            f"شاخص هم‌وزن {rpt.eq_index_change_pct:+.2f}% — بازار نزولی."
        )

    return rpt


def to_markdown(rpt: WeeklyReport) -> str:
    lines = [
        f"# گزارش هفتگی — {rpt.week_start.strftime('%Y-%m-%d')} تا {rpt.week_end.strftime('%Y-%m-%d')}",
        f"_تولید شده در {rpt.generated_at.strftime('%Y-%m-%d %H:%M')}_",
        "",
        "## ۱. Paper Trading",
        f"- باز شده: {rpt.paper_opened}",
        f"- بسته شده: {rpt.paper_closed}",
        f"- نرخ برد: {rpt.paper_win_rate}%",
        f"- میانگین R: {rpt.paper_avg_r:.3f}",
        f"- مجموع R: {rpt.paper_total_r:+.2f}",
        f"- Profit Factor: {rpt.paper_profit_factor:.2f}",
    ]
    if rpt.best_trades:
        lines.append("")
        lines.append("### بهترین معاملات")
        for t in rpt.best_trades:
            lines.append(f"- {t['symbol']} — R={t['r']:+.2f} ({t['strategy']})")
    if rpt.worst_trades:
        lines.append("")
        lines.append("### بدترین معاملات")
        for t in rpt.worst_trades:
            lines.append(f"- {t['symbol']} — R={t['r']:+.2f} ({t['strategy']})")

    lines.extend([
        "",
        "## ۲. وضعیت بازار",
        f"- روزهای سبز: {rpt.green_days}",
        f"- روزهای زرد: {rpt.yellow_days}",
        f"- روزهای قرمز: {rpt.red_days}",
    ])
    if rpt.eq_index_change_pct is not None:
        lines.append(
            f"- شاخص هم‌وزن: {rpt.eq_index_start:.2f} → {rpt.eq_index_end:.2f} "
            f"({rpt.eq_index_change_pct:+.2f}%)"
        )

    lines.extend([
        "",
        "## ۳. پرتفوی",
        f"- موقعیت‌های باز: {rpt.n_positions}",
        f"- ارزش کل: {rpt.portfolio_value:,.0f} ریال",
        f"- سود/زیان: {rpt.portfolio_pl_pct:+.2f}%",
        "",
        "## ۴. اخبار",
        f"- کل: {rpt.news_count}",
        f"- منفی: {rpt.news_negative_count}",
    ])

    if rpt.recommendations:
        lines.append("")
        lines.append("## ۵. توصیه‌ها")
        for r in rpt.recommendations:
            lines.append(f"- {r}")

    if rpt.warnings:
        lines.append("")
        lines.append("## ⚠️ هشدارها")
        for w in rpt.warnings:
            lines.append(f"- {w}")

    return "\n".join(lines)


def save_weekly_report(rpt: WeeklyReport) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    # ISO week number
    year, week, _ = rpt.week_end.isocalendar()
    fname = f"weekly_{year}-W{week:02d}.md"
    path = REPORT_DIR / fname
    path.write_text(to_markdown(rpt), encoding="utf-8")
    return path