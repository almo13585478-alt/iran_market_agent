"""
Ensemble voting: only enter when N-of-M strategies agree on the same bar.

Fee-aware design:
- rr_ratio is a top-level parameter (applied to all detectors uniformly)
- net_R at target (after round-trip fees) must be >= min_net_r
- min_atr_pct is a safety floor (default 0.15%), NOT the main decision rule
"""

from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from analysis.indicators import adx, atr, ema, macd, rsi, sma
from analysis.technical import MIN_BARS, analyze_prices
from backtesting.strategy_v2b import _soft_score
from decision_engine.scoring import compute_score


@dataclass
class EnsembleTrade:
    entry_ts: object
    exit_ts: object
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    exit_reason: str
    bars_held: int
    strategies: list[str] = field(default_factory=list)
    rr_ratio: float = 2.0


@dataclass
class EnsembleResult:
    symbol: str
    trades: list[EnsembleTrade] = field(default_factory=list)
    ok: bool = True
    message: str = ""
    stats: dict = field(default_factory=dict)
    rejected_by_fees: int = 0
    rejected_by_atr: int = 0
    avg_atr_pct_on_rejects: float = 0.0


def _detect_v2b(window: pd.DataFrame, symbol: str) -> dict | None:
    soft, _ = _soft_score(window, symbol)
    if soft < 5:
        return None
    a = analyze_prices(window)
    if not a.get("ok"):
        return None
    atr_val = a.get("atr14") or 0
    if atr_val <= 0:
        return None
    entry = float(window["close"].iloc[-1])
    stop = entry - 2.0 * atr_val
    if stop >= entry:
        return None
    return {"entry": entry, "stop": stop, "atr": atr_val}


def _detect_breakout(window: pd.DataFrame) -> dict | None:
    if len(window) < 42:
        return None
    prev = window.iloc[-41:-1]
    close = window["close"].astype(float)
    volume = window["volume"].astype(float).fillna(0.0)
    high_prev = float(prev["high"].max())
    vma = sma(volume, 20).iloc[-1]
    r_val = rsi(close, 14).iloc[-1]
    a_val = atr(window["high"].astype(float), window["low"].astype(float),
                close, 14).iloc[-1]
    if pd.isna(vma) or pd.isna(r_val) or pd.isna(a_val) or a_val <= 0:
        return None
    lc = float(close.iloc[-1])
    lv = float(volume.iloc[-1])
    if lc > high_prev * 1.001 and lv >= 1.5 * vma and 50 <= r_val <= 75:
        stop = max(high_prev * 0.99, lc - 1.5 * a_val)
        if stop < lc:
            return {"entry": lc, "stop": stop, "atr": float(a_val)}
    return None


def _detect_v2c(window: pd.DataFrame, symbol: str) -> dict | None:
    if len(window) < 152:
        return None
    close = window["close"].astype(float)
    open_ = window["open"].astype(float)
    volume = window["volume"].astype(float).fillna(0.0)
    high = window["high"].astype(float)
    low = window["low"].astype(float)

    ema_period = 150 if len(close) < 210 else 200
    e_fast = ema(close, 20).iloc[-1]
    e_mid = ema(close, 50).iloc[-1]
    e_slow = ema(close, ema_period).iloc[-1]
    if pd.isna(e_fast) or pd.isna(e_mid) or pd.isna(e_slow):
        return None
    if not (e_fast > e_mid > e_slow):
        return None

    lc = float(close.iloc[-1])
    if not (-0.02 <= (lc - e_fast) / e_fast <= 0.02):
        return None
    if lc <= float(open_.iloc[-1]):
        return None

    a_val = adx(high, low, close, 14).iloc[-1]
    if pd.isna(a_val) or a_val < 25.0:
        return None

    r_val = rsi(close, 14).iloc[-1]
    if pd.isna(r_val) or not (40.0 <= r_val <= 60.0):
        return None

    vma = sma(volume, 20).iloc[-1]
    if pd.isna(vma) or vma <= 0 or float(volume.iloc[-1]) < 0.8 * vma:
        return None

    macd_line, sig, hist = macd(close)
    if pd.isna(hist.iloc[-1]) or hist.iloc[-1] <= 0:
        return None
    if macd_line.iloc[-1] <= sig.iloc[-1]:
        return None

    analysis = analyze_prices(window)
    if not analysis.get("ok"):
        return None
    sc = compute_score(symbol, analysis)
    if sc.total < 60.0:
        return None

    atr_val = float(atr(high, low, close, 14).iloc[-1])
    if atr_val <= 0:
        return None
    return {"entry": lc, "stop": lc - 2.0 * atr_val, "atr": atr_val}


_DETECTORS = {
    "v2b": _detect_v2b,
    "breakout": _detect_breakout,
    "v2c": _detect_v2c,
}


def _apply_rr(sig: dict, rr_ratio: float) -> dict:
    """Override detector's target with requested rr_ratio."""
    entry = sig["entry"]
    stop = sig["stop"]
    risk = entry - stop
    if risk <= 0:
        return sig
    return {
        "entry": entry,
        "stop": stop,
        "target": entry + rr_ratio * risk,
        "atr": sig["atr"],
    }


def _net_r_at_target(entry, stop, target, fee_pct):
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return ((target - entry) - fee_pct * (entry + target)) / risk


def backtest_ensemble(
    symbol: str,
    df: pd.DataFrame,
    strategies: tuple = ("v2b", "breakout", "v2c"),
    min_agreement: int = 2,
    rr_ratio: float = 3.0,
    fee_pct: float = 0.005,
    min_bars: int = 150,
    min_net_r: float = 1.0,
    min_atr_pct: float = 0.0015,
) -> EnsembleResult:
    if df is None or df.empty:
        return EnsembleResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < min_bars + 2:
        return EnsembleResult(
            symbol=symbol, ok=False,
            message=f"داده کم (نیاز ≥{min_bars + 2}، موجود {len(df)})",
        )

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[EnsembleTrade] = []
    pos = None
    i = min_bars
    rejected_fees = 0
    rejected_atr = 0
    reject_atr_pcts: list[float] = []

    while i < len(df):
        bar = df.iloc[i]

        if pos is not None:
            hit_stop = bar["low"] <= pos["stop"]
            hit_target = bar["high"] >= pos["target"]
            exit_price = None
            reason = ""
            if hit_stop:
                exit_price, reason = pos["stop"], "stop"
            elif hit_target:
                exit_price, reason = pos["target"], "target"
            if exit_price is not None:
                net = (exit_price - pos["entry"]) - fee_pct * (
                    exit_price + pos["entry"]
                )
                risk = pos["entry"] - pos["stop"]
                trades.append(EnsembleTrade(
                    entry_ts=pos["entry_ts"], exit_ts=bar["ts"],
                    entry=pos["entry"], exit=exit_price,
                    stop=pos["stop"], target=pos["target"],
                    r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
                    exit_reason=reason, bars_held=i - pos["entry_idx"],
                    strategies=pos["strategies"], rr_ratio=rr_ratio,
                ))
                pos = None

        if pos is None:
            window = df.iloc[: i + 1]
            signals = {}
            for strat in strategies:
                detector = _DETECTORS.get(strat)
                if detector is None:
                    continue
                try:
                    sig = detector(window, symbol) if strat != "breakout" \
                        else detector(window)
                except Exception:
                    logger.exception("Detector {} failed at bar {}", strat, i)
                    continue
                if sig:
                    signals[strat] = _apply_rr(sig, rr_ratio)

            if len(signals) >= min_agreement:
                avg_entry = sum(s["entry"] for s in signals.values()) / len(signals)
                best_stop = max(s["stop"] for s in signals.values())
                avg_atr = sum(s["atr"] for s in signals.values()) / len(signals)

                atr_pct = avg_atr / avg_entry if avg_entry > 0 else 0
                if atr_pct < min_atr_pct:
                    rejected_atr += 1
                    reject_atr_pcts.append(atr_pct)
                    i += 1
                    continue

                avg_target = sum(s["target"] for s in signals.values()) / len(signals)
                net_r = _net_r_at_target(avg_entry, best_stop, avg_target, fee_pct)
                if net_r < min_net_r:
                    rejected_fees += 1
                    reject_atr_pcts.append(atr_pct)
                    i += 1
                    continue

                if best_stop < avg_entry and avg_target > avg_entry:
                    pos = {
                        "entry": avg_entry, "stop": best_stop,
                        "target": avg_target,
                        "entry_ts": bar["ts"], "entry_idx": i,
                        "strategies": list(signals.keys()),
                    }
        i += 1

    if pos is not None:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        net = (exit_price - pos["entry"]) - fee_pct * (
            exit_price + pos["entry"]
        )
        risk = pos["entry"] - pos["stop"]
        trades.append(EnsembleTrade(
            entry_ts=pos["entry_ts"], exit_ts=last["ts"],
            entry=pos["entry"], exit=exit_price,
            stop=pos["stop"], target=pos["target"],
            r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
            exit_reason="eod",
            bars_held=len(df) - 1 - pos["entry_idx"],
            strategies=pos["strategies"], rr_ratio=rr_ratio,
        ))

    stats = compute_ensemble_metrics(trades)
    msg = f"{len(trades)} معامله (rr={rr_ratio}, fee={fee_pct*100:.2f}%)"
    if rejected_fees or rejected_atr:
        msg += f" — رد شده: {rejected_fees} کارمزد، {rejected_atr} ATR کوچک"
    avg_reject = (sum(reject_atr_pcts) / len(reject_atr_pcts) * 100) if reject_atr_pcts else 0

    return EnsembleResult(
        symbol=symbol, trades=trades, ok=True, message=msg, stats=stats,
        rejected_by_fees=rejected_fees, rejected_by_atr=rejected_atr,
        avg_atr_pct_on_rejects=round(avg_reject, 3),
    )


def compute_ensemble_metrics(trades: list[EnsembleTrade]) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "avg_r": 0.0, "profit_factor": 0.0, "max_dd_r": 0.0,
                "strategies_used": {}}
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
    strat_used: dict[str, int] = {}
    for t in trades:
        key = "+".join(sorted(t.strategies))
        strat_used[key] = strat_used.get(key, 0) + 1
    return {
        "total": len(trades),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(pf, 2),
        "max_dd_r": round(mdd, 2),
        "strategies_used": strat_used,
    }