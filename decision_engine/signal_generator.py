"""
Trade signal generator with market-state and news awareness.

Threshold semantics changed in 11A:
- Old: 65 = raw score (was easy to hit)
- New: 70 = top 30% percentile in the universe (proper selectivity)
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal

from decision_engine.scoring import ScoreBreakdown


BUY_SCORE_THRESHOLD = 70.0
MAX_POSITION_PCT = 20.0


@dataclass
class TradeSignal:
    symbol: str
    action: Literal["BUY_CANDIDATE", "WATCH", "AVOID"]
    entry_low: float | None = None
    entry_high: float | None = None
    stop_loss: float | None = None
    target1: float | None = None
    target2: float | None = None
    position_size: int = 0
    risk_amount: float = 0.0
    reward_amount: float = 0.0
    rr_ratio: float | None = None
    reasons: list[str] = field(default_factory=list)
    invalidations: list[str] = field(default_factory=list)
    score: float = 0.0
    last_close: float | None = None
    computed_at: datetime = field(default_factory=datetime.now)


def generate_signal(
    score: ScoreBreakdown,
    portfolio_value: float,
    risk_per_trade_pct: float = 0.5,
    min_rr: float = 2.0,
    threshold: float = BUY_SCORE_THRESHOLD,
    market_state=None,
    recent_negative_news: bool = False,
) -> TradeSignal:
    tech = score.technical
    symbol = score.symbol

    if not tech or not tech.get("ok"):
        return TradeSignal(
            symbol=symbol, action="AVOID",
            reasons=["داده کافی برای تحلیل نیست"],
            invalidations=["داده تاریخی ناکافی"], score=score.total,
        )

    last_close = float(tech.get("last_close") or 0)
    atr = float(tech.get("atr14") or 0)
    support = float(tech.get("support") or 0)
    trend = tech.get("trend", "neutral")
    rsi = float(tech.get("rsi14") or 50)
    reasons = list(score.reasons) if score.reasons else []

    if market_state is not None:
        threshold = threshold + market_state.score_bonus
        if not market_state.trade_allowed:
            return TradeSignal(
                symbol=symbol, action="AVOID", score=score.total,
                last_close=last_close, reasons=reasons,
                invalidations=["وضعیت بازار قرمز شدید — معامله ممنوع"],
            )
    market_state_size = getattr(market_state, "size_multiplier", 1.0)

    if recent_negative_news:
        return TradeSignal(
            symbol=symbol, action="WATCH", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["خبر منفی اخیر (۳ روز گذشته)"],
        )

    if score.total < threshold:
        return TradeSignal(
            symbol=symbol, action="WATCH", score=score.total,
            last_close=last_close,
            reasons=reasons + [f"امتیاز {score.total:.0f} < آستانه {threshold:.0f}"],
            invalidations=["امتیاز کافی نیست"],
        )

    if trend == "down":
        return TradeSignal(
            symbol=symbol, action="AVOID", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["روند نزولی", "خرید در روند نزولی ممنوع"],
        )

    if rsi > 75:
        return TradeSignal(
            symbol=symbol, action="WATCH", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["RSI > 75"],
        )

    if last_close <= 0 or atr <= 0:
        return TradeSignal(
            symbol=symbol, action="AVOID", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["قیمت یا ATR نامعتبر"],
        )

    entry_low = round(last_close * 0.995, 2)
    entry_high = round(last_close * 1.005, 2)

    stop_by_atr = last_close - 2.0 * atr
    stop_by_support = support * 0.98 if support > 0 else last_close * 0.92
    stop_loss = round(max(stop_by_atr, stop_by_support), 2)
    if stop_loss >= entry_low:
        stop_loss = round(entry_low * 0.97, 2)
    if stop_loss >= last_close:
        return TradeSignal(
            symbol=symbol, action="AVOID", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["حد ضرر معتبر نیست"],
        )

    risk_per_share = last_close - stop_loss
    target1 = round(last_close + 2.0 * risk_per_share, 2)
    target2 = round(last_close + 3.0 * risk_per_share, 2)
    rr = (target1 - last_close) / risk_per_share if risk_per_share > 0 else 0.0

    if rr < min_rr:
        return TradeSignal(
            symbol=symbol, action="WATCH", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=[f"R/R = {rr:.2f} < {min_rr:.1f}"],
        )

    risk_amount_target = portfolio_value * (risk_per_trade_pct / 100.0) * market_state_size
    raw_size = int(risk_amount_target / risk_per_share) if risk_per_share > 0 else 0
    max_by_pct = int((portfolio_value * MAX_POSITION_PCT / 100.0) / last_close)
    position_size = min(raw_size, max_by_pct)

    if position_size <= 0:
        return TradeSignal(
            symbol=symbol, action="AVOID", score=score.total,
            last_close=last_close, reasons=reasons,
            invalidations=["حجم صفر — سرمایه کافی نیست"],
        )

    risk_amount = round(position_size * risk_per_share, 0)
    reward_amount = round(position_size * (target1 - last_close), 0)

    extra = []
    if market_state is not None and market_state.state != "green":
        extra.append(f"وضعیت بازار: {market_state.state} — حجم × {market_state_size}")

    return TradeSignal(
        symbol=symbol, action="BUY_CANDIDATE",
        entry_low=entry_low, entry_high=entry_high,
        stop_loss=stop_loss, target1=target1, target2=target2,
        position_size=position_size,
        risk_amount=risk_amount, reward_amount=reward_amount,
        rr_ratio=round(rr, 2),
        reasons=reasons + extra + [
            f"R/R = {rr:.2f}",
            f"ریسک هر سهم = {risk_per_share:,.0f}",
        ],
        invalidations=[
            f"شکست حد ضرر ({stop_loss:,.0f})",
            "RSI > 80",
            "خبر منفی ساختاری",
        ],
        score=score.total, last_close=last_close,
    )