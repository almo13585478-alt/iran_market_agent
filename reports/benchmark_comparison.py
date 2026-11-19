"""
Compare portfolio performance vs equal-weight market index.

Logic:
- Portfolio return: (current_value - cost) / cost × 100
- Index return: (last_snapshot.eq_index - first_snapshot.eq_index) / first_snapshot.eq_index × 100
- Alpha: portfolio_return - index_return
- days_tracked: based on snapshot dates (not transaction dates)
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import MarketSnapshot, PortfolioTransaction
from portfolio.holdings import compute_holdings


@dataclass
class BenchmarkComparison:
    portfolio_return_pct: float | None = None
    index_return_pct: float | None = None
    alpha_pct: float | None = None
    days_tracked: int = 0
    start_date: datetime | None = None
    end_date: datetime | None = None
    current_value: float = 0.0
    cost_basis: float = 0.0
    summary: str = ""
    verdict: str = "unknown"
    snapshots_used: int = 0


def compute_comparison(session: Session) -> BenchmarkComparison:
    rpt = BenchmarkComparison()

    holdings = compute_holdings(session)
    if not holdings:
        rpt.summary = "پرتفوی خالی است."
        return rpt

    first_tx = session.scalar(
        select(PortfolioTransaction)
        .order_by(PortfolioTransaction.ts)
        .limit(1)
    )
    if not first_tx:
        rpt.summary = "هیچ معامله‌ای ثبت نشده."
        return rpt

    current_value = sum(h.market_value for h in holdings)
    cost = sum(h.cost_basis for h in holdings)
    if cost <= 0:
        rpt.summary = "بهای تمام معتبر نیست."
        return rpt

    rpt.current_value = round(current_value, 0)
    rpt.cost_basis = round(cost, 0)
    rpt.portfolio_return_pct = round((current_value - cost) / cost * 100, 2)
    rpt.end_date = datetime.now()

    # --- Index return: earliest snapshot → latest snapshot ---
    snapshots = session.scalars(
        select(MarketSnapshot).order_by(MarketSnapshot.trade_date)
    ).all()
    rpt.snapshots_used = len(snapshots)

    if len(snapshots) >= 2:
        start_snap = snapshots[0]
        end_snap = snapshots[-1]
        rpt.start_date = start_snap.trade_date
        rpt.days_tracked = (end_snap.trade_date - start_snap.trade_date).days

        if start_snap.eq_index > 0:
            rpt.index_return_pct = round(
                (end_snap.eq_index - start_snap.eq_index)
                / start_snap.eq_index * 100,
                2,
            )
            rpt.alpha_pct = round(
                rpt.portfolio_return_pct - rpt.index_return_pct, 2,
            )
    elif len(snapshots) == 1:
        rpt.start_date = snapshots[0].trade_date
        rpt.days_tracked = 0

    # --- Verdict ---
    if rpt.alpha_pct is not None:
        if rpt.alpha_pct > 1:
            rpt.verdict = "outperforming"
            verdict_text = f"🟢 بهتر از بازار ({rpt.alpha_pct:+.2f}%)"
        elif rpt.alpha_pct < -1:
            rpt.verdict = "underperforming"
            verdict_text = f"🔴 بدتر از بازار ({rpt.alpha_pct:+.2f}%)"
        else:
            rpt.verdict = "tracking"
            verdict_text = f"⚖️ نزدیک به بازار ({rpt.alpha_pct:+.2f}%)"

        rpt.summary = (
            f"طی {rpt.days_tracked} روز ({rpt.snapshots_used} snapshot): "
            f"پرتفوی {rpt.portfolio_return_pct:+.2f}% "
            f"در برابر شاخص {rpt.index_return_pct:+.2f}%. "
            f"{verdict_text}"
        )
    elif len(snapshots) < 2:
        rpt.summary = (
            f"پرتفوی {rpt.portfolio_return_pct:+.2f}%. "
            f"برای مقایسه با شاخص، حداقل ۲ snapshot بازار لازم است "
            f"(فعلاً {len(snapshots)} ثبت شده). "
            f"از صفحه «📋 گزارش روزانه» snapshot بگیرید."
        )
    else:
        rpt.summary = (
            f"پرتفوی {rpt.portfolio_return_pct:+.2f}%. "
            f"شاخص قابل محاسبه نیست (eq_index نامعتبر)."
        )

    return rpt