"""
Group backtest for strategy v2b across multiple symbols.
Reuses v2b logic per symbol and aggregates.
"""

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backtesting.strategy_v2b import (
    backtest_v2b, compute_metrics, TradeV2b,
)
from database.models import Instrument, PriceBar


@dataclass
class V2GroupResult:
    symbols_ok: int = 0
    symbols_failed: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    max_dd_r: float = 0.0
    per_symbol: dict[str, dict] = field(default_factory=dict)
    all_trades: list[tuple[str, TradeV2b]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _load_df(session: Session, symbol: str) -> pd.DataFrame | None:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return None
    bars = (
        session.query(PriceBar)
        .filter_by(instrument_id=inst.id)
        .order_by(PriceBar.ts).all()
    )
    if not bars:
        return None
    return pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "value": b.value}
        for b in bars
    ])


def run_v2_group(
    session: Session,
    symbols: list[str],
    min_soft: int = 5,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,
    min_bars: int = 80,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> V2GroupResult:
    res = V2GroupResult()
    all_trades: list[TradeV2b] = []

    for idx, sym in enumerate(symbols, 1):
        df = _load_df(session, sym)
        if df is None or len(df) < min_bars + 2:
            res.symbols_failed += 1
            res.errors.append(f"{sym}: داده کم ({0 if df is None else len(df)} bars)")
            if progress_cb:
                progress_cb(idx, len(symbols), sym)
            continue

        try:
            bt = backtest_v2b(
                sym, df, min_soft=min_soft, atr_mult=atr_mult,
                rr_ratio=rr_ratio, fee_pct=fee_pct, min_bars=min_bars,
            )
        except Exception as e:
            res.symbols_failed += 1
            res.errors.append(f"{sym}: {e}")
            if progress_cb:
                progress_cb(idx, len(symbols), sym)
            continue

        if not bt.ok:
            res.symbols_failed += 1
            res.errors.append(f"{sym}: {bt.message}")
        else:
            res.symbols_ok += 1
            res.per_symbol[sym] = compute_metrics(bt.trades)
            for t in bt.trades:
                all_trades.append(t)
                res.all_trades.append((sym, t))
        if progress_cb:
            progress_cb(idx, len(symbols), sym)

    res.total_trades = len(all_trades)
    if all_trades:
        wins = [t for t in all_trades if t.r_multiple > 0]
        losses = [t for t in all_trades if t.r_multiple <= 0]
        res.wins = len(wins)
        res.losses = len(losses)
        res.win_rate = round(len(wins) / len(all_trades) * 100.0, 1)
        res.avg_r = round(
            sum(t.r_multiple for t in all_trades) / len(all_trades), 3
        )
        gw = sum(t.r_multiple for t in wins)
        gl = abs(sum(t.r_multiple for t in losses))
        res.profit_factor = round(gw / gl, 2) if gl > 0 else 99.99

        eq = [0.0]
        for t in all_trades:
            eq.append(eq[-1] + t.r_multiple)
        peak, mdd = eq[0], 0.0
        for v in eq:
            if v > peak:
                peak = v
            if peak - v > mdd:
                mdd = peak - v
        res.max_dd_r = round(mdd, 2)

    return res