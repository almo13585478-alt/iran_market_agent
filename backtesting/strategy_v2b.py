"""
Strategy v2b: relaxed pullback. 6 soft filters, ≥5 must pass.
Filter added: net_R at target must be >= min_net_r (rejects fee-eaten setups).
"""

from dataclasses import dataclass, field

import pandas as pd

from analysis.indicators import adx, ema, macd, rsi, sma
from analysis.technical import MIN_BARS, analyze_prices
from decision_engine.scoring import compute_score


@dataclass
class TradeV2b:
    entry_ts: object
    exit_ts: object
    entry: float
    exit: float
    stop: float
    target: float
    r_multiple: float
    exit_reason: str
    bars_held: int
    score: float


@dataclass
class V2bResult:
    symbol: str
    trades: list[TradeV2b] = field(default_factory=list)
    ok: bool = True
    message: str = ""
    rejected_by_fees: int = 0


def _soft_score(window: pd.DataFrame, symbol: str) -> tuple[int, dict]:
    close = window["close"].astype(float)
    open_ = window["open"].astype(float)
    volume = window["volume"].astype(float).fillna(0.0)
    high = window["high"].astype(float)
    low = window["low"].astype(float)

    if len(close) < MIN_BARS:
        return -1, {"reason": "داده کم"}

    e20 = ema(close, 20).iloc[-1]
    e50 = ema(close, 50).iloc[-1]
    if pd.isna(e20) or pd.isna(e50) or e20 <= e50:
        return -1, {"reason": "no uptrend"}
    if float(close.iloc[-1]) <= float(open_.iloc[-1]):
        return -1, {"reason": "candle not bullish"}

    score = 0
    diag = {}
    lc = float(close.iloc[-1])
    dist = (lc - e20) / e20
    if -0.05 <= dist <= 0.05:
        score += 1
        diag["near_ema20"] = True

    a = adx(high, low, close, 14).iloc[-1]
    if not pd.isna(a) and a >= 15:
        score += 1
        diag["adx"] = float(a)

    vma = sma(volume, 20).iloc[-1]
    if not pd.isna(vma) and vma > 0 and float(volume.iloc[-1]) >= 0.6 * vma:
        score += 1
        diag["vol_ok"] = True

    r = rsi(close, 14).iloc[-1]
    if not pd.isna(r) and 30 <= r <= 70:
        score += 1
        diag["rsi"] = float(r)

    macd_line, sig, hist = macd(close)
    if len(hist) and not pd.isna(hist.iloc[-1]) and hist.iloc[-1] > 0:
        score += 1
        diag["macd_pos"] = True

    analysis = analyze_prices(window)
    if analysis.get("ok"):
        sc = compute_score(symbol, analysis)
        if sc.total >= 50:
            score += 1
            diag["composite"] = sc.total

    return score, diag


def _net_r_at_target(entry: float, stop: float, target: float, fee_pct: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    gross = target - entry
    fees = fee_pct * (entry + target)
    net = gross - fees
    return net / risk


def backtest_v2b(
    symbol: str,
    df: pd.DataFrame,
    min_soft: int = 5,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,
    min_bars: int = 60,
    min_net_r: float = 1.0,
) -> V2bResult:
    if df is None or df.empty:
        return V2bResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < min_bars + 2:
        return V2bResult(symbol=symbol, ok=False, message="داده کم")

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[TradeV2b] = []
    pos = None
    rejected = 0
    i = min_bars

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
                trades.append(TradeV2b(
                    entry_ts=pos["entry_ts"], exit_ts=bar["ts"],
                    entry=pos["entry"], exit=exit_price,
                    stop=pos["stop"], target=pos["target"],
                    r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
                    exit_reason=reason, bars_held=i - pos["entry_idx"],
                    score=pos["score"],
                ))
                pos = None

        if pos is None:
            w = df.iloc[: i + 1]
            soft, _ = _soft_score(w, symbol)
            if soft >= min_soft:
                analysis = analyze_prices(w)
                if analysis.get("ok"):
                    atr_val = analysis.get("atr14") or 0
                    if atr_val > 0:
                        entry = float(bar["close"])
                        stop = entry - atr_mult * atr_val
                        if stop < entry:
                            target = entry + rr_ratio * (entry - stop)
                            net_r = _net_r_at_target(entry, stop, target, fee_pct)
                            if net_r >= min_net_r:
                                pos = {
                                    "entry": entry, "stop": stop,
                                    "target": target,
                                    "entry_ts": bar["ts"], "entry_idx": i,
                                    "score": analysis.get("score", 0.0),
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
        trades.append(TradeV2b(
            entry_ts=pos["entry_ts"], exit_ts=last["ts"],
            entry=pos["entry"], exit=exit_price,
            stop=pos["stop"], target=pos["target"],
            r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
            exit_reason="eod",
            bars_held=len(df) - 1 - pos["entry_idx"],
            score=pos["score"],
        ))

    msg = f"{len(trades)} معامله"
    if rejected:
        msg += f" (رد شده: {rejected} کارمزد)"
    return V2bResult(symbol=symbol, trades=trades, ok=True,
                     message=msg, rejected_by_fees=rejected)


def compute_metrics(trades: list[TradeV2b]) -> dict:
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