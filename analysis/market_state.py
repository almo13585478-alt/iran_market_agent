"""
Market state classifier (master prompt §16).

Classifies the whole market as GREEN / YELLOW / RED based on 5 independent
signals. No single indicator decides — all contribute.

Signals:
1. Breadth today: % of active symbols with close > prev_close
2. 5-day breadth: % of symbols with 5-day return > 0
3. Trend breadth: % of symbols with close > EMA50
4. Volume ratio: today's total volume / 20-day MA
5. Cross-sectional volatility: std of today's % changes

Rules (conservative):
- RED if breadth_today < 30% OR trend_breadth < 30% OR vol_ratio > 2.5
- GREEN if breadth_today > 55% AND trend_breadth > 55% AND vol_ratio in [0.8, 2.0]
- YELLOW otherwise

In RED markets:
- New position size is halved
- Score threshold is raised by +5
- Cash is preferred
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.indicators import ema
from database.models import Instrument, PriceBar


@dataclass
class MarketState:
    state: str = "yellow"           # green | yellow | red
    score: float = 0.0              # 0 = worst, 100 = best
    breadth_today: float = 0.0      # %
    breadth_5d: float = 0.0         # %
    trend_breadth: float = 0.0      # % above EMA50
    vol_ratio: float = 1.0          # today / 20d MA
    vol_cross: float = 0.0          # std of % changes
    n_symbols: int = 0
    reasons: list[str] = field(default_factory=list)
    computed_at: datetime = field(default_factory=datetime.now)

    # Derived actions
    size_multiplier: float = 1.0    # 1.0 in green, 0.5 in yellow/red
    score_bonus: float = 0.0        # add to thresholds
    trade_allowed: bool = True      # false in extreme red


def _last_two_bars(session: Session) -> dict[int, tuple[PriceBar, PriceBar | None]]:
    """Return {inst_id: (last_bar, prev_bar)} for instruments with >= 2 bars."""
    out = {}
    inst_ids = [
        r[0] for r in session.execute(
            select(Instrument.id).where(Instrument.is_active.is_(True))
        ).all()
    ]
    for iid in inst_ids:
        bars = (
            session.query(PriceBar)
            .filter_by(instrument_id=iid)
            .order_by(PriceBar.ts.desc())
            .limit(2)
            .all()
        )
        if len(bars) >= 2:
            out[iid] = (bars[0], bars[1])
    return out


def classify_market(session: Session, lookback: int = 5) -> MarketState:
    """
    Compute market state from all active symbols.
    Uses last bar vs previous bar for breadth, EMA50 for trend.
    """
    st = MarketState()

    pairs = _last_two_bars(session)
    if not pairs:
        st.state = "red"
        st.reasons.append("هیچ نمادی با دو کندل اخیر وجود ندارد")
        st.score_bonus = 10
        st.size_multiplier = 0.5
        return st

    # 1) Breadth today
    up_today = 0
    changes = []
    volumes_last = []
    for iid, (last, prev) in pairs.items():
        if prev.close > 0:
            chg = (last.close - prev.close) / prev.close
            changes.append(chg)
            if chg > 0:
                up_today += 1
        volumes_last.append(last.volume)

    st.n_symbols = len(pairs)
    st.breadth_today = round(up_today / len(pairs) * 100.0, 1)

    # 2) 5-day breadth + EMA50 trend
    up_5d = 0
    above_ema50 = 0
    counted_5d = 0
    counted_ema = 0
    for iid in pairs.keys():
        bars = (
            session.query(PriceBar)
            .filter_by(instrument_id=iid)
            .order_by(PriceBar.ts.desc())
            .limit(60)
            .all()
        )
        if len(bars) < lookback + 1:
            continue
        closes_asc = pd.Series([b.close for b in reversed(bars)], dtype=float)
        if closes_asc.iloc[-lookback - 1] > 0:
            r5 = (closes_asc.iloc[-1] - closes_asc.iloc[-lookback - 1]) / closes_asc.iloc[-lookback - 1]
            counted_5d += 1
            if r5 > 0:
                up_5d += 1
        if len(closes_asc) >= 50:
            ema50 = ema(closes_asc, 50).iloc[-1]
            if not pd.isna(ema50):
                counted_ema += 1
                if closes_asc.iloc[-1] > ema50:
                    above_ema50 += 1

    st.breadth_5d = round(up_5d / counted_5d * 100.0, 1) if counted_5d else 0.0
    st.trend_breadth = round(above_ema50 / counted_ema * 100.0, 1) if counted_ema else 0.0

    # 3) Volume ratio (today total / previous 20d average total)
    total_last = sum(volumes_last)
    # Approximate prev-20d total using the last bar of each instrument * 20
    # (best we can do without per-day aggregates)
    avg_vol_est = total_last  # fallback
    try:
        # Use actual: mean of last 20 bars per instrument / current total
        sample_vols = []
        for iid in list(pairs.keys())[:200]:
            bars = (
                session.query(PriceBar)
                .filter_by(instrument_id=iid)
                .order_by(PriceBar.ts.desc())
                .limit(21)
                .all()
            )
            if len(bars) >= 21:
                prior = sum(b.volume for b in bars[1:21]) / 20.0
                if prior > 0:
                    sample_vols.append(bars[0].volume / prior)
        if sample_vols:
            st.vol_ratio = round(float(np.median(sample_vols)), 2)
        else:
            st.vol_ratio = 1.0
    except Exception:
        st.vol_ratio = 1.0

    # 4) Cross-sectional volatility
    st.vol_cross = round(float(np.std(changes)) * 100.0, 2) if changes else 0.0

    # ---- Classify ----
    is_red = (
        st.breadth_today < 30.0
        or st.trend_breadth < 30.0
        or st.vol_ratio > 2.5
        or st.vol_cross > 4.0
    )
    is_green = (
        st.breadth_today > 55.0
        and st.trend_breadth > 55.0
        and 0.8 <= st.vol_ratio <= 2.0
        and st.vol_cross <= 2.5
    )

    if is_red:
        st.state = "red"
        st.size_multiplier = 0.5
        st.score_bonus = 5.0
        st.trade_allowed = st.breadth_today >= 15.0  # truly catastrophic = no trades
    elif is_green:
        st.state = "green"
        st.size_multiplier = 1.0
        st.score_bonus = 0.0
        st.trade_allowed = True
    else:
        st.state = "yellow"
        st.size_multiplier = 0.75
        st.score_bonus = 3.0
        st.trade_allowed = True

    # ---- Score 0-100 ----
    s = 50.0
    s += (st.breadth_today - 50.0) * 0.4
    s += (st.breadth_5d - 50.0) * 0.2
    s += (st.trend_breadth - 50.0) * 0.3
    if st.vol_ratio > 2.0:
        s -= 10.0
    if st.vol_cross > 3.0:
        s -= 10.0
    st.score = round(max(0.0, min(100.0, s)), 1)

    # ---- Reasons ----
    if st.state == "red":
        if st.breadth_today < 30:
            st.reasons.append(f"عرض بازار ضعیف امروز ({st.breadth_today}%)")
        if st.trend_breadth < 30:
            st.reasons.append(f"کمتر از ۳۰٪ نمادها بالای EMA50 ({st.trend_breadth}%)")
        if st.vol_ratio > 2.5:
            st.reasons.append(f"حجم بالا ({st.vol_ratio}×) — احتمال هیجان/فروش")
        if st.vol_cross > 4.0:
            st.reasons.append(f"نوسان عرضی بالا ({st.vol_cross}%)")
    elif st.state == "green":
        st.reasons.append(
            f"عرض قوی ({st.breadth_today}% مثبت، "
            f"{st.trend_breadth}% بالای EMA50)"
        )
    else:
        st.reasons.append("سیگنال‌های مختلط — وضعیت خنثی")

    return st