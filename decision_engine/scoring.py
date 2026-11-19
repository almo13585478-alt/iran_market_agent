"""
Scoring engine with percentile-based ranking.

Two-stage design:
1. compute_score(symbol, analysis, fundamental) → raw component scores (0-100)
2. rank_universe(scores) → percentile-normalized total (0-100)

Percentile total means:
- 92 = top 8% of universe
- 70 = top 30%
- 50 = median
- 30 = bottom 30%

This fixes the saturation problem where 70% of stocks scored above 70.
"""

import math
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


DEFAULT_WEIGHTS = {
    "technical": 30.0,
    "momentum": 20.0,
    "liquidity": 15.0,
    "risk": 15.0,
    "trend_strength": 10.0,
    "fundamental": 10.0,
}


@dataclass
class ScoreBreakdown:
    symbol: str
    total: float = 0.0
    technical_score: float = 0.0
    liquidity_score: float = 0.0
    momentum_score: float = 0.0
    risk_score: float = 0.0
    trend_strength_score: float = 0.0
    fundamental_score: float = 50.0
    weights: dict[str, float] = field(default_factory=lambda: dict(DEFAULT_WEIGHTS))
    reasons: list[str] = field(default_factory=list)
    red_flags: list[str] = field(default_factory=list)
    technical: dict[str, Any] = field(default_factory=dict)
    computed_at: datetime = field(default_factory=datetime.now)


# ---------------------------------------------------------------------------
# Raw component scorers (0-100, no ceiling stacking)
# ---------------------------------------------------------------------------

def _technical_score(analysis: dict) -> float:
    trend = analysis.get("trend", "neutral")
    rsi = float(analysis.get("rsi14") or 50)
    adx = float(analysis.get("adx14") or 0)
    macd_hist = float(analysis.get("macd_hist") or 0)

    score = 50.0
    if trend == "up":
        score += 15.0
    elif trend == "down":
        score -= 15.0

    if 40 <= rsi <= 60:
        score += 8.0
    elif rsi < 30:
        score += 5.0
    elif rsi > 75:
        score -= 10.0

    if macd_hist > 0:
        score += 7.0
    else:
        score -= 5.0

    if adx >= 30:
        score += 10.0
    elif adx >= 20:
        score += 5.0
    elif adx < 15:
        score -= 5.0

    return max(0.0, min(100.0, score))


def _liquidity_score(analysis: dict) -> float:
    vol_ma = analysis.get("vol_ma20") or 0
    if vol_ma <= 0:
        return 0.0
    return max(0.0, min(100.0, 20.0 * math.log10(max(vol_ma, 1))))


def _momentum_score(analysis: dict) -> float:
    rsi = float(analysis.get("rsi14") or 50)
    macd_hist = float(analysis.get("macd_hist") or 0)
    macd_line = float(analysis.get("macd") or 0)
    signal = float(analysis.get("macd_signal") or 0)

    score = 50.0
    if 45 <= rsi <= 65:
        score += 10.0
    elif rsi < 30:
        score += 5.0
    elif rsi > 75:
        score -= 10.0

    if macd_hist > 0 and macd_line > signal:
        score += 15.0
    elif macd_hist < 0:
        score -= 10.0

    return max(0.0, min(100.0, score))


def _risk_score(analysis: dict) -> float:
    atr_pct = analysis.get("atr_pct")
    if atr_pct is None:
        return 40.0
    if atr_pct <= 1.0:
        return 90.0
    if atr_pct <= 2.0:
        return 80.0
    if atr_pct <= 4.0:
        return 60.0
    if atr_pct <= 6.0:
        return 40.0
    return 20.0


def _trend_strength_score(analysis: dict) -> float:
    adx = float(analysis.get("adx14") or 0)
    ema50 = analysis.get("ema50")
    last = float(analysis.get("last_close") or 0)
    score = 50.0
    if adx >= 30:
        score += 25.0
    elif adx >= 20:
        score += 15.0
    elif adx < 15:
        score -= 15.0
    if ema50 and ema50 > 0 and last > 0:
        dist = (last - ema50) / ema50
        if 0.02 < dist < 0.10:
            score += 15.0
        elif dist >= 0.10:
            score -= 10.0
    return max(0.0, min(100.0, score))


def _fundamental_score(fundamental: dict | None) -> float:
    """0-100 from fundamental data. Returns 50 if no data."""
    if not fundamental:
        return 50.0

    score = 50.0
    count = 0

    pe = fundamental.get("pe")
    if pe is not None and pe > 0:
        count += 1
        if pe < 5:
            score += 15.0
        elif pe < 10:
            score += 10.0
        elif pe < 15:
            score += 5.0
        elif pe > 50:
            score -= 20.0
        elif pe > 30:
            score -= 10.0

    roe = fundamental.get("roe")
    if roe is not None:
        count += 1
        if roe > 30:
            score += 15.0
        elif roe > 20:
            score += 10.0
        elif roe > 10:
            score += 5.0
        elif roe < 0:
            score -= 15.0

    profit_growth = fundamental.get("profit_growth")
    if profit_growth is not None:
        count += 1
        if profit_growth > 50:
            score += 15.0
        elif profit_growth > 20:
            score += 10.0
        elif profit_growth > 0:
            score += 5.0
        elif profit_growth < -20:
            score -= 15.0

    debt_ratio = fundamental.get("debt_ratio")
    if debt_ratio is not None:
        count += 1
        if debt_ratio < 30:
            score += 10.0
        elif debt_ratio > 70:
            score -= 15.0

    if count == 0:
        return 50.0

    return max(0.0, min(100.0, score))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def compute_score(
    symbol: str,
    analysis: dict,
    fundamental: dict | None = None,
) -> ScoreBreakdown:
    """Compute raw component scores (0-100, NOT yet percentile-normalized)."""
    if not analysis.get("ok"):
        return ScoreBreakdown(
            symbol=symbol,
            reasons=[analysis.get("message", "تحلیل ناموفق")],
            red_flags=["عدم امکان تحلیل"],
            technical=analysis,
        )

    tech = _technical_score(analysis)
    liq = _liquidity_score(analysis)
    mom = _momentum_score(analysis)
    risk = _risk_score(analysis)
    trend = _trend_strength_score(analysis)
    fund = _fundamental_score(fundamental)

    reasons = list(analysis.get("signals", []))[:5]
    red_flags: list[str] = []
    if analysis.get("trend") == "down":
        red_flags.append("روند نزولی")
    if (analysis.get("rsi14") or 0) > 75:
        red_flags.append("RSI در اشباع خرید")
    if (analysis.get("atr_pct") or 0) > 6.0:
        red_flags.append("نوسان بالا (ATR% > 6)")
    if not analysis.get("vol_ma20") or analysis.get("vol_ma20", 0) < 100_000:
        red_flags.append("نقدشوندگی پایین")

    w = DEFAULT_WEIGHTS
    weighted = (
        tech * w["technical"]
        + mom * w["momentum"]
        + liq * w["liquidity"]
        + risk * w["risk"]
        + trend * w["trend_strength"]
        + fund * w["fundamental"]
    )
    total_w = sum(w.values())
    raw_total = round(weighted / total_w, 1)

    return ScoreBreakdown(
        symbol=symbol,
        total=raw_total,
        technical_score=round(tech, 1),
        liquidity_score=round(liq, 1),
        momentum_score=round(mom, 1),
        risk_score=round(risk, 1),
        trend_strength_score=round(trend, 1),
        fundamental_score=round(fund, 1),
        reasons=reasons,
        red_flags=red_flags,
        technical=analysis,
    )


def rank_universe(scores: list[ScoreBreakdown]) -> list[ScoreBreakdown]:
    """
    Return NEW list with each component converted to a percentile rank
    within `scores`, and total recomputed as weighted percentile.

    The returned ScoreBreakdown.total is now:
    - 100 = best in universe
    - 50 = median
    - 0 = worst in universe
    """
    if not scores:
        return scores
    n = len(scores)
    if n == 1:
        return scores

    component_fields = [
        "technical_score",
        "liquidity_score",
        "momentum_score",
        "risk_score",
        "trend_strength_score",
        "fundamental_score",
    ]

    out = [
        ScoreBreakdown(
            symbol=s.symbol,
            total=s.total,
            technical_score=s.technical_score,
            liquidity_score=s.liquidity_score,
            momentum_score=s.momentum_score,
            risk_score=s.risk_score,
            trend_strength_score=s.trend_strength_score,
            fundamental_score=s.fundamental_score,
            weights=dict(s.weights),
            reasons=list(s.reasons),
            red_flags=list(s.red_flags),
            technical=s.technical,
            computed_at=s.computed_at,
        )
        for s in scores
    ]

    for field_name in component_fields:
        pairs = sorted(
            [(i, getattr(s, field_name)) for i, s in enumerate(out)],
            key=lambda x: x[1],
        )
        for rank, (i, _) in enumerate(pairs):
            pct = (rank / (n - 1)) * 100.0
            setattr(out[i], field_name, round(pct, 1))

    w = out[0].weights
    total_w = sum(w.values())
    for s in out:
        weighted = (
            s.technical_score * w["technical"]
            + s.momentum_score * w["momentum"]
            + s.liquidity_score * w["liquidity"]
            + s.risk_score * w["risk"]
            + s.trend_strength_score * w["trend_strength"]
            + s.fundamental_score * w["fundamental"]
        )
        s.total = round(weighted / total_w, 1)

    return out