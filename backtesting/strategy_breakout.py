"""
Strategy Breakout with fee-aware entry filter.
"""

from dataclasses import dataclass, field

import pandas as pd

from analysis.indicators import atr, rsi, sma


@dataclass
class TradeBO:
    entry_ts: object
    exit_ts: object
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    exit_reason: str
    bars_held: int


@dataclass
class BOResult:
    symbol: str
    trades: list[TradeBO] = field(default_factory=list)
    ok: bool = True
    message: str = ""
    rejected_by_fees: int = 0


def _net_r_at_target(entry, stop, target, fee_pct):
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return ((target - entry) - fee_pct * (entry + target)) / risk


def backtest_breakout(
    symbol: str,
    df: pd.DataFrame,
    lookback: int = 40,
    vol_mult: float = 1.5,
    atr_mult: float = 1.5,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,
    min_bars: int = 60,
    min_net_r: float = 1.0,
) -> BOResult:
    if df is None or df.empty:
        return BOResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < max(min_bars, lookback + 2):
        return BOResult(symbol=symbol, ok=False, message="داده کم")

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[TradeBO] = []
    pos = None
    rejected = 0
    i = max(lookback, min_bars)

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
                trades.append(TradeBO(
                    entry_ts=pos["entry_ts"], exit_ts=bar["ts"],
                    entry=pos["entry"], exit=exit_price,
                    stop=pos["stop"], target=pos["target"],
                    r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
                    exit_reason=reason, bars_held=i - pos["entry_idx"],
                ))
                pos = None

        if pos is None:
            w = df.iloc[i - lookback : i + 1]
            prev = df.iloc[i - lookback : i]
            close = w["close"].astype(float)
            high_prev = float(prev["high"].max())
            volume = w["volume"].astype(float).fillna(0.0)
            vma = sma(volume, 20).iloc[-1]
            rsi_val = rsi(close, 14).iloc[-1]
            atr_val = atr(
                w["high"].astype(float), w["low"].astype(float), close, 14,
            ).iloc[-1]

            if pd.isna(vma) or pd.isna(rsi_val) or pd.isna(atr_val) or atr_val <= 0:
                i += 1
                continue

            lc = float(close.iloc[-1])
            lv = float(volume.iloc[-1])

            if (lc > high_prev * 1.001 and lv >= vol_mult * vma
                    and 50 <= rsi_val <= 75):
                stop = max(high_prev * 0.99, lc - atr_mult * atr_val)
                if stop < lc:
                    target = lc + rr_ratio * (lc - stop)
                    net_r = _net_r_at_target(lc, stop, target, fee_pct)
                    if net_r >= min_net_r:
                        pos = {
                            "entry": lc, "stop": stop, "target": target,
                            "entry_ts": bar["ts"], "entry_idx": i,
                        }
                    else:
                        rejected += 1
        i += 1

    if pos is not None:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        net = (exit_price - pos["entry"]) - fee_pct * (
            exit_price + pos["entry"]
        )
        risk = pos["entry"] - pos["stop"]
        trades.append(TradeBO(
            entry_ts=pos["entry_ts"], exit_ts=last["ts"],
            entry=pos["entry"], exit=exit_price,
            stop=pos["stop"], target=pos["target"],
            r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
            exit_reason="eod",
            bars_held=len(df) - 1 - pos["entry_idx"],
        ))

    msg = f"{len(trades)} معامله"
    if rejected:
        msg += f" (رد شده: {rejected} کارمزد)"
    return BOResult(symbol=symbol, trades=trades, ok=True,
                    message=msg, rejected_by_fees=rejected)


def compute_bo_metrics(trades: list[TradeBO]) -> dict:
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0,
                "avg_r": 0.0, "profit_factor": 0.0, "max_dd_r": 0.0}
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
        "total": len(trades), "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "avg_r": round(avg_r, 3), "profit_factor": round(pf, 2),
        "max_dd_r": round(mdd, 2),
    }