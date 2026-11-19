"""
Portfolio risk advisor: concentration, daily VaR, position sizing guidance.
"""

from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Instrument, PriceBar
from portfolio.holdings import compute_holdings


@dataclass
class RiskAdvisorReport:
    hhi: float = 0.0
    top_position_pct: float = 0.0
    top_industry_pct: float = 0.0
    n_positions: int = 0

    daily_var_95_worst: float = 0.0
    daily_var_95_independent: float = 0.0
    daily_var_pct_worst: float = 0.0

    portfolio_value: float = 0.0
    current_open_risk_pct: float = 0.0
    remaining_risk_budget_pct: float = 0.0
    max_new_position_value: float = 0.0

    warnings: list[str] = field(default_factory=list)
    actions: list[str] = field(default_factory=list)
    summary: str = ""
    computed_at: datetime = field(default_factory=datetime.now)


def _daily_return_std(session: Session, instrument_id: int, days: int = 30) -> float | None:
    bars = (
        session.query(PriceBar)
        .filter(PriceBar.instrument_id == instrument_id)
        .order_by(PriceBar.ts.desc())
        .limit(days + 1)
        .all()
    )
    if len(bars) < 10:
        return None
    closes = [b.close for b in reversed(bars)]
    if any(c <= 0 for c in closes):
        return None
    returns = [(closes[i] - closes[i - 1]) / closes[i - 1] for i in range(1, len(closes))]
    if not returns:
        return None
    return float(np.std(returns, ddof=1))


def _atr14(session: Session, instrument_id: int) -> float | None:
    bars = (
        session.query(PriceBar)
        .filter(PriceBar.instrument_id == instrument_id)
        .order_by(PriceBar.ts.desc())
        .limit(15)
        .all()
    )
    if len(bars) < 15:
        return None
    trs = []
    for i in range(1, len(bars)):
        tr = max(
            bars[i - 1].high - bars[i - 1].low,
            abs(bars[i - 1].high - bars[i].close),
            abs(bars[i - 1].low - bars[i].close),
        )
        trs.append(tr)
    if not trs:
        return None
    return float(np.mean(trs))


def compute_risk_report(
    session: Session,
    portfolio_value: float,
    max_total_risk_pct: float = 1.5,
    max_single_industry_pct: float = 40.0,
    z_score: float = 1.65,
) -> RiskAdvisorReport:
    rpt = RiskAdvisorReport(portfolio_value=portfolio_value)

    holdings = compute_holdings(session)
    if not holdings:
        rpt.summary = "پرتفوی خالی است — ریسکی وجود ندارد."
        return rpt

    rpt.n_positions = len(holdings)
    total_mv = sum(h.market_value for h in holdings) or 1.0

    weights = [h.market_value / total_mv for h in holdings]
    rpt.hhi = round(sum((w * 100) ** 2 for w in weights), 1)
    rpt.top_position_pct = round(max(weights) * 100, 1)

    # Industry concentration
    industries: dict[str, float] = {}
    for h in holdings:
        inst = session.scalar(select(Instrument).where(Instrument.symbol == h.symbol))
        ind = (inst.industry.strip() if inst and inst.industry else "نامشخص")
        industries[ind] = industries.get(ind, 0.0) + h.market_value
    if industries:
        rpt.top_industry_pct = round(max(industries.values()) / total_mv * 100, 1)

    # VaR
    var_terms = []
    for h in holdings:
        inst = session.scalar(select(Instrument).where(Instrument.symbol == h.symbol))
        if not inst:
            continue
        std = _daily_return_std(session, inst.id, days=30)
        if std is None or std <= 0:
            continue
        var_i = h.market_value * std * z_score
        var_terms.append(var_i)

    if var_terms:
        rpt.daily_var_95_worst = round(sum(var_terms), 0)
        rpt.daily_var_95_independent = round(
            float(np.sqrt(sum(v ** 2 for v in var_terms))), 0,
        )
        rpt.daily_var_pct_worst = round(rpt.daily_var_95_worst / total_mv * 100, 2)

    # Open risk budget
    open_risk = 0.0
    for h in holdings:
        inst = session.scalar(select(Instrument).where(Instrument.symbol == h.symbol))
        if not inst:
            continue
        atr = _atr14(session, inst.id)
        if atr is None or atr <= 0:
            continue
        risk_per_share = 2.0 * atr
        open_risk += h.quantity * risk_per_share

    rpt.current_open_risk_pct = round(open_risk / portfolio_value * 100, 2) \
        if portfolio_value > 0 else 0.0
    rpt.remaining_risk_budget_pct = round(
        max(0.0, max_total_risk_pct - rpt.current_open_risk_pct), 2,
    )

    typical_risk = 0.5
    if rpt.remaining_risk_budget_pct >= typical_risk:
        rpt.max_new_position_value = round(portfolio_value * typical_risk / 100, 0)
    else:
        rpt.max_new_position_value = round(
            portfolio_value * rpt.remaining_risk_budget_pct / 100, 0,
        )

    # Warnings
    if rpt.top_position_pct > 30:
        rpt.warnings.append(
            f"تمرکز بالا: {rpt.top_position_pct}% سبد در یک سهم."
        )
    if rpt.top_industry_pct > max_single_industry_pct:
        rpt.warnings.append(
            f"تمرکز صنعتی بالا: {rpt.top_industry_pct}% سبد در یک صنعت."
        )
    if rpt.hhi > 3000:
        rpt.warnings.append(
            f"شاخص HHI {rpt.hhi:.0f} — تمرکز قابل توجه."
        )
    if rpt.daily_var_pct_worst > 5.0:
        rpt.warnings.append(
            f"VaR روزانه ۹۵٪ (بدترین): {rpt.daily_var_pct_worst}% — "
            f"~{rpt.daily_var_95_worst:,.0f} ریال در یک روز بد."
        )
    if rpt.remaining_risk_budget_pct <= 0:
        rpt.warnings.append(
            "بودجه ریسک پر شده — پوزیشن جدید باز نکنید تا یکی بسته شود."
        )

    # Actions
    if rpt.n_positions < 3:
        rpt.actions.append("تنوع کم — تعداد موقعیت کمتر از ۳ است.")
    elif rpt.n_positions > 10:
        rpt.actions.append(
            f"{rpt.n_positions} موقعیت — پیگیری سخت می‌شود."
        )
    if rpt.top_position_pct > 40:
        rpt.actions.append(
            f"کاهش وزن بزرگ‌ترین موقعیت از {rpt.top_position_pct}% به زیر ۳۰٪."
        )
    if rpt.top_industry_pct > 60:
        rpt.actions.append(
            f"تنوع صنعتی: صنعت غالب {rpt.top_industry_pct}% سبد است."
        )
    if rpt.remaining_risk_budget_pct >= 0.5:
        rpt.actions.append(
            f"می‌توانید پوزیشن جدید تا {rpt.max_new_position_value:,.0f} ریال اضافه کنید."
        )

    rpt.summary = (
        f"پرتفوی {rpt.n_positions} موقعیت. "
        f"تمرکز: {rpt.top_position_pct}% بزرگ‌ترین سهم، "
        f"{rpt.top_industry_pct}% بزرگ‌ترین صنعت. "
    )
    if rpt.daily_var_pct_worst > 0:
        rpt.summary += (
            f"VaR روزانه ۹۵٪: {rpt.daily_var_pct_worst}% "
            f"(~{rpt.daily_var_95_worst:,.0f} ریال). "
        )
    rpt.summary += (
        f"بودجه ریسک مصرف‌شده: {rpt.current_open_risk_pct}% "
        f"از {max_total_risk_pct}%."
    )

    return rpt