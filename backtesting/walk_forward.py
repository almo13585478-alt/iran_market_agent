"""
Walk-forward validator.

Takes a single parameter set (usually the winner from grid search) and:
1. Splits each symbol's bars into TRAIN (first 70%) and TEST (last 30%)
2. Runs backtest on TRAIN and TEST separately
3. Compares: if TEST is much worse than TRAIN, the combo is overfit.

Verdicts:
- "robust": TEST has >= min_trades and test_avgR >= 0.5 * train_avgR
- "fragile": TEST positive but degrades badly (0 < test_avgR < 0.5 * train)
- "overfit": TEST_avgR < 0 (train was a lie)
- "unknown": not enough test trades to judge
"""

from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from backtesting.simple_backtest import (
    backtest_symbol, compute_metrics,
)
from database.models import Instrument, PriceBar


@dataclass
class WalkForwardResult:
    threshold: float
    atr_mult: float
    rr_ratio: float
    train_trades: int = 0
    train_win_rate: float = 0.0
    train_avg_r: float = 0.0
    train_profit_factor: float = 0.0
    test_trades: int = 0
    test_win_rate: float = 0.0
    test_avg_r: float = 0.0
    test_profit_factor: float = 0.0
    degradation: float = 0.0          # test_avgR / train_avgR (if train>0)
    verdict: str = "unknown"
    symbols_train_ok: int = 0
    symbols_test_ok: int = 0
    errors: list[str] = field(default_factory=list)


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
        {"ts": b.ts, "open": b.open, "high": b.high,
         "low": b.low, "close": b.close,
         "volume": b.volume, "value": b.value}
        for b in bars
    ])


def split_df(df: pd.DataFrame, train_pct: float = 0.70) -> tuple[pd.DataFrame, pd.DataFrame]:
    n = len(df)
    split = int(n * train_pct)
    return df.iloc[:split].reset_index(drop=True), df.iloc[split:].reset_index(drop=True)


def walk_forward_combo(
    session: Session,
    symbols: list[str],
    threshold: float,
    atr_mult: float,
    rr_ratio: float,
    fee_pct: float = 0.005,
    min_bars: int = 60,
    train_pct: float = 0.70,
    min_test_trades: int = 3,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> WalkForwardResult:
    result = WalkForwardResult(
        threshold=threshold, atr_mult=atr_mult, rr_ratio=rr_ratio,
    )

    all_train_trades = []
    all_test_trades = []

    for idx, sym in enumerate(symbols, 1):
        df = _load_df(session, sym)
        if df is None or len(df) < min_bars * 2:
            result.errors.append(f"{sym}: داده کافی برای walk-forward نیست")
            if progress_cb:
                progress_cb(idx, len(symbols), sym)
            continue

        train_df, test_df = split_df(df, train_pct)

        try:
            bt_train = backtest_symbol(
                sym, train_df, threshold=threshold,
                atr_mult=atr_mult, rr_ratio=rr_ratio,
                fee_pct=fee_pct, min_bars=min_bars,
            )
            if bt_train.ok:
                all_train_trades.extend(bt_train.trades)
                result.symbols_train_ok += 1
        except Exception as e:
            result.errors.append(f"{sym} train: {e}")

        # For test, we need historical context — but only count trades opened
        # in the test window. We pass the full df but filter trades whose
        # entry_ts falls in the test window.
        try:
            bt_full = backtest_symbol(
                sym, df, threshold=threshold,
                atr_mult=atr_mult, rr_ratio=rr_ratio,
                fee_pct=fee_pct, min_bars=min_bars,
            )
            if bt_full.ok:
                test_start_ts = test_df["ts"].iloc[0] if len(test_df) else None
                test_trades = [
                    t for t in bt_full.trades
                    if test_start_ts is not None and t.entry_ts >= test_start_ts
                ]
                all_test_trades.extend(test_trades)
                result.symbols_test_ok += 1
        except Exception as e:
            result.errors.append(f"{sym} test: {e}")

        if progress_cb:
            progress_cb(idx, len(symbols), sym)

    # Aggregate train
    result.train_trades = len(all_train_trades)
    if all_train_trades:
        m = compute_metrics(all_train_trades)
        result.train_win_rate = m["win_rate"]
        result.train_avg_r = m["avg_r"]
        result.train_profit_factor = m["profit_factor"]

    # Aggregate test
    result.test_trades = len(all_test_trades)
    if all_test_trades:
        m = compute_metrics(all_test_trades)
        result.test_win_rate = m["win_rate"]
        result.test_avg_r = m["avg_r"]
        result.test_profit_factor = m["profit_factor"]

    # Verdict
    if result.test_trades < min_test_trades:
        result.verdict = "unknown"
    elif result.train_avg_r <= 0:
        result.verdict = "overfit"  # train itself was bad
    else:
        result.degradation = round(
            result.test_avg_r / result.train_avg_r, 2
        )
        if result.test_avg_r < 0:
            result.verdict = "overfit"
        elif result.degradation >= 0.5:
            result.verdict = "robust"
        else:
            result.verdict = "fragile"

    return result


def run_walk_forward_top(
    session: Session,
    symbols: list[str],
    combos: list[tuple[float, float, float]],
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> list[WalkForwardResult]:
    """Run walk-forward on a list of (threshold, atr_mult, rr_ratio)."""
    results: list[WalkForwardResult] = []
    for i, (thr, atr, rr) in enumerate(combos, 1):
        label = f"thr={thr} atr={atr} rr={rr}"
        if progress_cb:
            progress_cb(i, len(combos), label)
        results.append(walk_forward_combo(
            session, symbols, threshold=thr, atr_mult=atr, rr_ratio=rr,
        ))
    return results