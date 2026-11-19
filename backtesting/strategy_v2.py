"""
Strategy v2: pullback in uptrend (master prompt Strategy A).

Entry conditions (ALL must be true):
- EMA20 > EMA50 (established uptrend)
- close within [EMA20 * 0.97, EMA20 * 1.03] (pullback near EMA20)
- last bar is bullish (close > open)
- ADX14 >= 20 (market is trending, not choppy)
- volume >= 0.7 * vol_MA20 (not drying up)
- RSI in [35, 65] (neither oversold nor overbought)
- composite score >= threshold (still respect scoring)

Exit: ATR-based stop / target, same as v1.

Rationale vs v1: v1 entered whenever score was high regardless of price
location. v2 requires the price to be near a specific level (EMA20) with
a bullish confirmation candle. This drastically reduces trade count but
should improve win rate and R.
"""

from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from analysis.indicators import adx, atr, ema, rsi, sma
from analysis.technical import MIN_BARS, analyze_prices
from decision_engine.scoring import compute_score


@dataclass
class TradeV2:
    entry_ts: Any
    exit_ts: Any
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    exit_reason: str
    bars_held: int
    score: float


@dataclass
class V2BacktestResult:
    symbol: str
    trades: list[TradeV2] = field(default_factory=list)
    ok: bool = True
    message: str = ""


def _passes_v2_filters(window: pd.DataFrame) -> tuple[bool, dict]:
    """Return (pass, diagnostic dict) on the last bar of `window`."""
    close = window["close"].astype(float)
    open_ = window["open"].astype(float)
    volume = window["volume"].astype(float).fillna(0.0)
    high = window["high"].astype(float)
    low = window["low"].astype(float)

    if len(close) < MIN_BARS:
        return False, {"reason": "داده کم"}

    ema20 = ema(close, 20).iloc[-1]
    ema50 = ema(close, 50).iloc[-1]
    if pd.isna(ema20) or pd.isna(ema50) or ema20 <= ema50:
        return False, {"reason": "روند صعودی نیست"}

    last_close = float(close.iloc[-1])
    dist_to_ema20 = (last_close - ema20) / ema20
    if not (-0.03 <= dist_to_ema20 <= 0.03):
        return False, {"reason": f"دور از EMA20 ({dist_to_ema20*100:.1f}%)"}

    if close.iloc[-1] <= open_.iloc[-1]:
        return False, {"reason": "کندل نزولی"}

    adx_series = adx(high, low, close, 14)
    last_adx = float(adx_series.iloc[-1]) if not pd.isna(adx_series.iloc[-1]) else 0.0
    if last_adx < 20.0:
        return False, {"reason": f"ADX پایین ({last_adx:.1f})"}

    vol_ma = sma(volume, 20).iloc[-1]
    if pd.isna(vol_ma) or vol_ma <= 0:
        return False, {"reason": "حجم نامعتبر"}
    if float(volume.iloc[-1]) < 0.7 * vol_ma:
        return False, {"reason": "حجم خشک"}

    rsi_val = float(rsi(close, 14).iloc[-1])
    if not (35.0 <= rsi_val <= 65.0):
        return False, {"reason": f"RSI خارج بازه ({rsi_val:.1f})"}

    return True, {"adx": last_adx, "rsi": rsi_val, "dist_pct": dist_to_ema20 * 100}


def backtest_v2(
    symbol: str,
    df: pd.DataFrame,
    threshold: float = 55.0,
    atr_mult: float = 2.5,
    rr_ratio: float = 2.5,
    fee_pct: float = 0.005,
    min_bars: int = 60,
) -> V2BacktestResult:
    if df is None or df.empty:
        return V2BacktestResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < min_bars + 2:
        return V2BacktestResult(
            symbol=symbol, ok=False,
            message=f"داده کافی نیست (نیاز ≥{min_bars + 2})",
        )

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[TradeV2] = []
    position: dict | None = None
    i = min_bars
    while i < len(df):
        bar = df.iloc[i]

        if position is not None:
            hit_stop = bar["low"] <= position["stop"]
            hit_target = bar["high"] >= position["target"]
            exit_price = None
            reason = ""
            if hit_stop:
                exit_price, reason = position["stop"], "stop"
            elif hit_target:
                exit_price, reason = position["target"], "target"
            if exit_price is not None:
                net = (exit_price - position["entry"]) - fee_pct * (
                    exit_price + position["entry"]
                )
                risk = position["entry"] - position["stop"]
                trades.append(TradeV2(
                    entry_ts=position["entry_ts"],
                    exit_ts=bar["ts"],
                    entry=position["entry"], exit=exit_price,
                    stop=position["stop"], target=position["target"],
                    r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
                    exit_reason=reason,
                    bars_held=i - position["entry_idx"],
                    score=position["score"],
                ))
                position = None

        if position is None:
            window = df.iloc[: i + 1]
            ok_filter, _diag = _passes_v2_filters(window)
            if ok_filter:
                analysis = analyze_prices(window)
                if analysis.get("ok"):
                    sc = compute_score(symbol, analysis)
                    atr_val = analysis.get("atr14") or 0
                    if sc.total >= threshold and atr_val > 0:
                        entry = float(bar["close"])
                        stop = entry - atr_mult * atr_val
                        if stop < entry:
                            position = {
                                "entry": entry, "stop": stop,
                                "target": entry + rr_ratio * (entry - stop),
                                "entry_ts": bar["ts"], "entry_idx": i,
                                "score": sc.total,
                            }
        i += 1

    if position is not None:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        net = (exit_price - position["entry"]) - fee_pct * (
            exit_price + position["entry"]
        )
        risk = position["entry"] - position["stop"]
        trades.append(TradeV2(
            entry_ts=position["entry_ts"], exit_ts=last["ts"],
            entry=position["entry"], exit=exit_price,
            stop=position["stop"], target=position["target"],
            r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
            exit_reason="eod",
            bars_held=len(df) - 1 - position["entry_idx"],
            score=position["score"],
        ))

    return V2BacktestResult(
        symbol=symbol, trades=trades, ok=True, message=f"{len(trades)} معامله",
    )


def compute_v2_metrics(trades: list[TradeV2]) -> dict:
    if not trades:
        return {
            "total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
            "avg_r": 0.0, "profit_factor": 0.0, "max_dd_r": 0.0,
        }
    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]
    avg_r = sum(t.r_multiple for t in trades) / len(trades)
    gw = sum(t.r_multiple for t in wins)
    gl = abs(sum(t.r_multiple for t in losses))
    pf = gw / gl if gl > 0 else 99.99
    eq = [0.0]
    for t in trades:
        eq.append(eq[-1] + t.r_multiple)
    peak, mdd = eq[0], 0.0
    for v in eq:
        if v > peak:
            peak = v
        if peak - v > mdd:
            mdd = peak - v
    return {
        "total": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(pf, 2),
        "max_dd_r": round(mdd, 2),
    }