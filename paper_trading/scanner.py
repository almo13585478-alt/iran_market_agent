"""
Daily signal scanner for paper trading.

Runs the v2b / breakout detection on the LAST bar of each eligible symbol
and saves new signals to paper_signals. Never re-emits a signal for the
same symbol/strategy on the same day.
"""

from datetime import datetime
from typing import Callable

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from analysis.indicators import adx, atr, ema, macd, rsi, sma
from analysis.technical import MIN_BARS, analyze_prices
from backtesting.strategy_v2b import _soft_score
from database.audit import log_event
from database.models import Instrument, PaperSignal, PriceBar


def _load_df(session: Session, inst_id: int) -> pd.DataFrame | None:
    bars = (
        session.query(PriceBar)
        .filter_by(instrument_id=inst_id)
        .order_by(PriceBar.ts)
        .all()
    )
    if not bars:
        return None
    return pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "value": b.value}
        for b in bars
    ])


def detect_v2b_on_last_bar(
    symbol: str, df: pd.DataFrame,
    min_soft: int = 5,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
) -> dict | None:
    """Return a signal dict if the last bar triggers v2b entry, else None."""
    if df is None or len(df) < MIN_BARS:
        return None
    soft, diag = _soft_score(df, symbol)
    if soft < min_soft:
        return None
    analysis = analyze_prices(df)
    if not analysis.get("ok"):
        return None
    atr_val = analysis.get("atr14") or 0
    if atr_val <= 0:
        return None
    entry = float(df["close"].iloc[-1])
    stop = entry - atr_mult * atr_val
    if stop >= entry:
        return None
    target = entry + rr_ratio * (entry - stop)
    return {
        "strategy": "v2b",
        "entry": entry, "stop": stop, "target": target,
        "atr": atr_val,
        "score": analysis.get("score", 0.0),
        "soft": soft,
        "diag": diag,
        "last_ts": df["ts"].iloc[-1],
    }


def detect_breakout_on_last_bar(
    symbol: str, df: pd.DataFrame,
    lookback: int = 40,
    vol_mult: float = 1.5,
    atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
) -> dict | None:
    """Return a breakout signal if the last bar triggers entry."""
    if df is None or len(df) < max(MIN_BARS, lookback + 2):
        return None
    if len(df) < lookback + 2:
        return None

    prev = df.iloc[-(lookback + 1):-1]
    close = df["close"].astype(float)
    volume = df["volume"].astype(float).fillna(0.0)
    high_prev = float(prev["high"].max())
    vma = sma(volume, 20).iloc[-1]
    rsi_val = rsi(close, 14).iloc[-1]
    atr_val = atr(
        df["high"].astype(float), df["low"].astype(float), close, 14
    ).iloc[-1]
    if pd.isna(vma) or pd.isna(rsi_val) or pd.isna(atr_val) or atr_val <= 0:
        return None
    lc = float(close.iloc[-1])
    lv = float(volume.iloc[-1])
    if lc > high_prev * 1.001 and lv >= vol_mult * vma and 50 <= rsi_val <= 75:
        stop = max(high_prev * 0.99, lc - atr_mult * atr_val)
        if stop < lc:
            return {
                "strategy": "breakout",
                "entry": lc, "stop": stop,
                "target": lc + rr_ratio * (lc - stop),
                "atr": float(atr_val),
                "score": 0.0, "soft": 0,
                "diag": {"rsi": float(rsi_val), "vol_ratio": lv / vma},
                "last_ts": df["ts"].iloc[-1],
            }
    return None


def _already_signed_today(
    session: Session, inst_id: int, strategy: str, when: datetime
) -> bool:
    day_start = datetime(when.year, when.month, when.day)
    return session.scalar(
        select(PaperSignal.id)
        .where(PaperSignal.instrument_id == inst_id)
        .where(PaperSignal.strategy == strategy)
        .where(PaperSignal.signal_ts >= day_start)
        .limit(1)
    ) is not None


def scan(
    session: Session,
    strategies: list[str] = None,
    min_soft: int = 5,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    min_bars: int = 80,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> dict:
    """Scan all eligible active symbols and create new PaperSignal rows."""
    strategies = strategies or ["v2b"]
    results = {"new": 0, "existing": 0, "scanned": 0, "errors": []}
    now = datetime.now()

    eligible = session.scalars(
        select(Instrument).where(Instrument.is_active.is_(True))
        .order_by(Instrument.symbol)
    ).all()

    symbols_with_count = {}
    for inst in eligible:
        cnt = session.query(PriceBar).filter_by(instrument_id=inst.id).count()
        symbols_with_count[inst.id] = cnt

    eligible = [i for i in eligible if symbols_with_count.get(i.id, 0) >= min_bars]
    total = len(eligible)

    for idx, inst in enumerate(eligible, 1):
        df = _load_df(session, inst.id)
        if df is None:
            if progress_cb:
                progress_cb(idx, total, inst.symbol)
            continue

        results["scanned"] += 1

        for strat in strategies:
            if _already_signed_today(session, inst.id, strat, now):
                results["existing"] += 1
                continue

            try:
                if strat == "v2b":
                    sig = detect_v2b_on_last_bar(
                        inst.symbol, df, min_soft=min_soft,
                        atr_mult=atr_mult, rr_ratio=rr_ratio,
                    )
                elif strat == "breakout":
                    sig = detect_breakout_on_last_bar(inst.symbol, df)
                else:
                    continue
            except Exception as e:
                results["errors"].append(f"{inst.symbol}/{strat}: {e}")
                continue

            if sig is None:
                continue

            session.add(PaperSignal(
                instrument_id=inst.id,
                symbol=inst.symbol,
                strategy=strat,
                signal_ts=now,
                entry_price=sig["entry"],
                stop_loss=sig["stop"],
                target=sig["target"],
                atr_at_signal=sig["atr"],
                score_at_signal=sig["score"],
                soft_filters_passed=sig["soft"],
                status="open",
            ))
            results["new"] += 1

        if progress_cb:
            progress_cb(idx, total, inst.symbol)

    session.commit()
    log_event(
        session, "scan", "paper_scan",
        subject=",".join(strategies),
        details=f"scanned={results['scanned']} new={results['new']} "
                f"existing={results['existing']} errors={len(results['errors'])}",
    )
    return results