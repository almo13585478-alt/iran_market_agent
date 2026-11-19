"""
Simple, honest backtester. Runs a walk-forward over a single symbol.

Rules (conservative — no look-ahead bias):
- At bar i, analysis uses only df[:i+1].
- Entry executed at close of bar i.
- Stop / target checks START at bar i+1, never on the entry bar.
- If both stop and target could be hit on the same bar, STOP wins.
- Fees are subtracted from entry and exit price.
- If a position is still open at the last bar, it is closed at last close.

Strategy:
- Enter when: composite score >= threshold AND trend != "down" AND RSI < 75
- Stop = entry - atr_mult * ATR
- Target = entry + rr_ratio * (entry - stop)
- No pyramiding, one position at a time.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd
from loguru import logger

from analysis.technical import analyze_prices
from decision_engine.scoring import compute_score


@dataclass
class Trade:
    entry_ts: datetime
    exit_ts: datetime
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    exit_reason: str    # "stop" | "target" | "eod"
    bars_held: int
    score: float


@dataclass
class BacktestResult:
    symbol: str
    trades: list[Trade] = field(default_factory=list)
    ok: bool = True
    message: str = ""
    params: dict[str, Any] = field(default_factory=dict)


def backtest_symbol(
    symbol: str,
    df: pd.DataFrame,
    threshold: float = 65.0,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,     # 0.5% round-trip default (broker + tax approx)
    min_bars: int = 60,
) -> BacktestResult:
    if df is None or df.empty:
        return BacktestResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < min_bars + 2:
        return BacktestResult(
            symbol=symbol,
            ok=False,
            message=f"داده کافی نیست (نیاز: ≥{min_bars + 2}، موجود: {len(df)})",
        )

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[Trade] = []
    position: dict | None = None

    i = min_bars
    while i < len(df):
        bar = df.iloc[i]

        # ---- EXIT CHECK (only if in position) ----
        if position is not None:
            hit_stop = bar["low"] <= position["stop"]
            hit_target = bar["high"] >= position["target"]

            exit_price: float | None = None
            reason = ""
            if hit_stop:                        # STOP wins if both touched
                exit_price = position["stop"]
                reason = "stop"
            elif hit_target:
                exit_price = position["target"]
                reason = "target"

            if exit_price is not None:
                gross_pnl = exit_price - position["entry"]
                fees = (exit_price + position["entry"]) * fee_pct
                net_pnl = gross_pnl - fees
                risk_per_share = position["entry"] - position["stop"]
                r_mult = net_pnl / risk_per_share if risk_per_share > 0 else 0.0
                trades.append(Trade(
                    entry_ts=position["entry_ts"],
                    exit_ts=bar["ts"],
                    entry=position["entry"],
                    exit=exit_price,
                    stop=position["stop"],
                    target=position["target"],
                    r_multiple=round(r_mult, 3),
                    exit_reason=reason,
                    bars_held=i - position["entry_idx"],
                    score=position["score"],
                ))
                position = None

        # ---- ENTRY CHECK (only if flat) ----
        if position is None:
            window = df.iloc[: i + 1]
            analysis = analyze_prices(window)
            if analysis.get("ok"):
                score = compute_score(symbol, analysis)
                rsi = analysis.get("rsi14") or 50
                atr = analysis.get("atr14") or 0
                trend = analysis.get("trend", "neutral")

                if (
                    score.total >= threshold
                    and trend != "down"
                    and rsi < 75
                    and atr > 0
                ):
                    entry = float(bar["close"])
                    stop = entry - atr_mult * atr
                    if stop < entry:
                        target = entry + rr_ratio * (entry - stop)
                        position = {
                            "entry": entry,
                            "stop": stop,
                            "target": target,
                            "entry_ts": bar["ts"],
                            "entry_idx": i,
                            "score": score.total,
                        }

        i += 1

    # ---- Force-close any remaining position at last bar ----
    if position is not None:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        gross_pnl = exit_price - position["entry"]
        fees = (exit_price + position["entry"]) * fee_pct
        net_pnl = gross_pnl - fees
        risk_per_share = position["entry"] - position["stop"]
        r_mult = net_pnl / risk_per_share if risk_per_share > 0 else 0.0
        trades.append(Trade(
            entry_ts=position["entry_ts"],
            exit_ts=last["ts"],
            entry=position["entry"],
            exit=exit_price,
            stop=position["stop"],
            target=position["target"],
            r_multiple=round(r_mult, 3),
            exit_reason="eod",
            bars_held=len(df) - 1 - position["entry_idx"],
            score=position["score"],
        ))

    return BacktestResult(
        symbol=symbol,
        trades=trades,
        ok=True,
        message=f"{len(trades)} معامله",
        params={
            "threshold": threshold,
            "atr_mult": atr_mult,
            "rr_ratio": rr_ratio,
            "fee_pct": fee_pct,
            "bars": len(df),
        },
    )


def compute_metrics(trades: list[Trade]) -> dict[str, Any]:
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0,
            "max_dd_r": 0.0, "avg_bars": 0.0, "expectancy": 0.0,
        }

    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]
    win_rate = len(wins) / len(trades) * 100.0
    avg_r = sum(t.r_multiple for t in trades) / len(trades)

    gross_win = sum(t.r_multiple for t in wins)
    gross_loss = abs(sum(t.r_multiple for t in losses))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else float("inf")

    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.r_multiple)
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = peak - v
        if dd > max_dd:
            max_dd = dd

    avg_bars = sum(t.bars_held for t in trades) / len(trades)

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(win_rate, 1),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(profit_factor, 2) if profit_factor != float("inf") else 99.99,
        "max_dd_r": round(max_dd, 2),
        "avg_bars": round(avg_bars, 1),
        "expectancy": round(avg_r, 3),
    }