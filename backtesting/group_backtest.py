"""
Group backtest: run the same strategy across N symbols and aggregate
portfolio-level statistics.
"""

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from loguru import logger
from sqlalchemy import select, func
from sqlalchemy.orm import Session

from backtesting.simple_backtest import (
    backtest_symbol, compute_metrics, Trade,
)
from database.models import Instrument, PriceBar


@dataclass
class GroupResult:
    symbols_tested: int = 0
    symbols_ok: int = 0
    symbols_failed: int = 0
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    max_dd_r: float = 0.0
    expectancy: float = 0.0
    avg_bars: float = 0.0
    per_symbol: dict[str, dict] = field(default_factory=dict)
    all_trades: list[tuple[str, Trade]] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    params: dict = field(default_factory=dict)


def _load_df(session: Session, symbol: str) -> pd.DataFrame | None:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return None
    bars = (
        session.query(PriceBar)
        .filter_by(instrument_id=inst.id)
        .order_by(PriceBar.ts)
        .all()
    )
    if not bars:
        return None
    return pd.DataFrame([
        {
            "ts": b.ts, "open": b.open, "high": b.high,
            "low": b.low, "close": b.close,
            "volume": b.volume, "value": b.value,
        }
        for b in bars
    ])


def run_group_backtest(
    session: Session,
    symbols: list[str],
    threshold: float = 65.0,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,
    min_bars: int = 60,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> GroupResult:
    result = GroupResult(params={
        "threshold": threshold,
        "atr_mult": atr_mult,
        "rr_ratio": rr_ratio,
        "fee_pct": fee_pct,
        "min_bars": min_bars,
    })
    result.symbols_tested = len(symbols)

    all_trades: list[Trade] = []

    for idx, sym in enumerate(symbols, 1):
        df = _load_df(session, sym)
        if df is None or len(df) < min_bars + 2:
            result.symbols_failed += 1
            result.errors.append(f"{sym}: داده کافی نیست")
            if progress_cb:
                progress_cb(idx, len(symbols), sym)
            continue

        try:
            bt = backtest_symbol(
                sym, df,
                threshold=threshold,
                atr_mult=atr_mult,
                rr_ratio=rr_ratio,
                fee_pct=fee_pct,
                min_bars=min_bars,
            )
        except Exception as e:
            result.symbols_failed += 1
            result.errors.append(f"{sym}: {e}")
            logger.exception("Backtest failed for {}", sym)
            if progress_cb:
                progress_cb(idx, len(symbols), sym)
            continue

        if not bt.ok:
            result.symbols_failed += 1
            result.errors.append(f"{sym}: {bt.message}")
        else:
            result.symbols_ok += 1
            sym_metrics = compute_metrics(bt.trades)
            result.per_symbol[sym] = sym_metrics
            for t in bt.trades:
                all_trades.append(t)
                result.all_trades.append((sym, t))

        if progress_cb:
            progress_cb(idx, len(symbols), sym)

    result.total_trades = len(all_trades)
    if result.total_trades:
        wins = [t for t in all_trades if t.r_multiple > 0]
        losses = [t for t in all_trades if t.r_multiple <= 0]
        result.wins = len(wins)
        result.losses = len(losses)
        result.win_rate = round(len(wins) / len(all_trades) * 100.0, 1)
        result.avg_r = round(
            sum(t.r_multiple for t in all_trades) / len(all_trades), 3
        )

        gross_win = sum(t.r_multiple for t in wins)
        gross_loss = abs(sum(t.r_multiple for t in losses))
        result.profit_factor = (
            round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99
        )

        equity = [0.0]
        for t in all_trades:
            equity.append(equity[-1] + t.r_multiple)
        peak, max_dd = equity[0], 0.0
        for v in equity:
            if v > peak:
                peak = v
            dd = peak - v
            if dd > max_dd:
                max_dd = dd
        result.max_dd_r = round(max_dd, 2)

        result.expectancy = result.avg_r
        result.avg_bars = round(
            sum(t.bars_held for t in all_trades) / len(all_trades), 1
        )

    return result


# ---------------- Diagnostic + selection helpers ----------------

def pick_top_eligible_symbols(
    session: Session, min_bars: int = 60, limit: int = 100
) -> list[str]:
    """
    Return symbols whose bar count >= min_bars, ordered by symbol.
    Fixed SQL: only Instrument.symbol is selected (not PriceBar.id).
    """
    rows = session.execute(
        select(Instrument.symbol)
        .join(PriceBar, PriceBar.instrument_id == Instrument.id)
        .where(Instrument.is_active.is_(True))
        .group_by(Instrument.id)
        .having(func.count(PriceBar.id) >= min_bars)
        .order_by(Instrument.symbol)
        .limit(limit)
    ).all()
    return [r[0] for r in rows]


def get_symbol_bar_counts(
    session: Session, min_bars: int = 0, limit: int = 20
) -> list[tuple[str, int]]:
    """Return (symbol, bar_count) sorted by bar_count desc."""
    q = (
        select(Instrument.symbol, func.count(PriceBar.id).label("n"))
        .join(PriceBar, PriceBar.instrument_id == Instrument.id)
        .where(Instrument.is_active.is_(True))
        .group_by(Instrument.id)
    )
    if min_bars > 0:
        q = q.having(func.count(PriceBar.id) >= min_bars)
    q = q.order_by(func.count(PriceBar.id).desc()).limit(limit)
    rows = session.execute(q).all()
    return [(r[0], int(r[1])) for r in rows]


def max_bars_available(session: Session) -> int:
    """Return the maximum bar count across all instruments (0 if empty)."""
    row = session.execute(
        select(func.count(PriceBar.id).label("n"))
        .group_by(PriceBar.instrument_id)
        .order_by(func.count(PriceBar.id).desc())
        .limit(1)
    ).first()
    return int(row[0]) if row else 0


def count_eligible(session: Session, min_bars: int) -> int:
    """Return number of instruments with >= min_bars."""
    row = session.execute(
        select(func.count(PriceBar.id).label("n"))
        .join(Instrument, Instrument.id == PriceBar.instrument_id)
        .where(Instrument.is_active.is_(True))
        .group_by(PriceBar.instrument_id)
        .having(func.count(PriceBar.id) >= min_bars)
    ).all()
    return len(row)