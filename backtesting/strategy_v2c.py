"""
Strategy v2c: STRICT pullback. ALL filters must pass + fee-aware.

Design notes (v2):
- Adaptive slow EMA: uses EMA200 when enough history (>=210 bars),
  otherwise EMA150. This allows the strategy to run on symbols that
  only have ~155 bars of history (e.g. after a 200-day sync).
- Fee-aware: net_R at target (after round-trip fees) must be >= min_net_r.
  Rejects low-ATR setups where the fee eats the entire target gain.

Filters (ALL required):
1. EMA20 > EMA50 > EMA_slow  (adaptive: 200 or 150)
2. |close - EMA20| / EMA20 <= 2%
3. Bullish candle (close > open)
4. ADX(14) >= 25
5. RSI(14) in [40, 60]
6. volume >= 0.8 * SMA(volume, 20)
7. MACD hist > 0 AND MACD > signal
8. composite score >= 60
"""

from dataclasses import dataclass, field

import pandas as pd

from analysis.indicators import adx, atr, ema, macd, rsi, sma
from analysis.technical import analyze_prices
from decision_engine.scoring import compute_score


# Minimum bars required to run v2c at all.
# 152 = 150 (slow EMA) + 2 (need at least 2 points after the EMA warmup)
MIN_BARS_V2C = 152


@dataclass
class TradeV2c:
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
class V2cResult:
    symbol: str
    trades: list[TradeV2c] = field(default_factory=list)
    ok: bool = True
    message: str = ""
    rejected_by_fees: int = 0
    rejected_by_filters: int = 0


def _net_r_at_target(entry: float, stop: float, target: float, fee_pct: float) -> float:
    risk = entry - stop
    if risk <= 0:
        return 0.0
    return ((target - entry) - fee_pct * (entry + target)) / risk


def _passes_strict_filters(window: pd.DataFrame, symbol: str) -> dict | None:
    """
    Return signal dict if ALL 8 strict filters pass on last bar, else None.
    Requires window to have >= MIN_BARS_V2C rows.
    """
    if len(window) < MIN_BARS_V2C:
        return None

    close = window["close"].astype(float)
    open_ = window["open"].astype(float)
    volume = window["volume"].astype(float).fillna(0.0)
    high = window["high"].astype(float)
    low = window["low"].astype(float)

    # --- 1. Adaptive triple EMA alignment ---
    ema_period = 200 if len(close) >= 210 else 150
    e20 = ema(close, 20).iloc[-1]
    e50 = ema(close, 50).iloc[-1]
    e_slow = ema(close, ema_period).iloc[-1]
    if pd.isna(e20) or pd.isna(e50) or pd.isna(e_slow):
        return None
    if not (e20 > e50 > e_slow):
        return None

    # --- 2. Tight to EMA20 ---
    lc = float(close.iloc[-1])
    if lc <= 0:
        return None
    if not (-0.02 <= (lc - e20) / e20 <= 0.02):
        return None

    # --- 3. Bullish candle ---
    if lc <= float(open_.iloc[-1]):
        return None

    # --- 4. ADX ---
    a_val = adx(high, low, close, 14).iloc[-1]
    if pd.isna(a_val) or a_val < 25.0:
        return None

    # --- 5. RSI ---
    r_val = rsi(close, 14).iloc[-1]
    if pd.isna(r_val) or not (40.0 <= r_val <= 60.0):
        return None

    # --- 6. Volume ---
    vma = sma(volume, 20).iloc[-1]
    if pd.isna(vma) or vma <= 0:
        return None
    if float(volume.iloc[-1]) < 0.8 * vma:
        return None

    # --- 7. MACD ---
    macd_line, sig, hist = macd(close)
    if pd.isna(hist.iloc[-1]) or hist.iloc[-1] <= 0:
        return None
    if macd_line.iloc[-1] <= sig.iloc[-1]:
        return None

    # --- 8. Composite score ---
    analysis = analyze_prices(window)
    if not analysis.get("ok"):
        return None
    sc = compute_score(symbol, analysis)
    if sc.total < 60.0:
        return None

    atr_val = float(atr(high, low, close, 14).iloc[-1])
    if atr_val <= 0:
        return None

    return {
        "entry": lc,
        "atr": atr_val,
        "score": sc.total,
        "adx": float(a_val),
        "rsi": float(r_val),
        "ema_slow_period": ema_period,
    }


def backtest_v2c(
    symbol: str,
    df: pd.DataFrame,
    atr_mult: float = 2.0,
    rr_ratio: float = 2.0,
    fee_pct: float = 0.005,
    min_bars: int = 150,
    min_net_r: float = 1.0,
) -> V2cResult:
    if df is None or df.empty:
        return V2cResult(symbol=symbol, ok=False, message="داده خالی")
    if len(df) < max(min_bars, MIN_BARS_V2C):
        return V2cResult(
            symbol=symbol, ok=False,
            message=(
                f"داده کافی نیست (نیاز ≥{max(min_bars, MIN_BARS_V2C)}، "
                f"موجود {len(df)})"
            ),
        )

    df = df.sort_values("ts").reset_index(drop=True)
    trades: list[TradeV2c] = []
    pos = None
    rejected_by_fees = 0
    rejected_by_filters = 0
    i = max(min_bars, MIN_BARS_V2C)

    while i < len(df):
        bar = df.iloc[i]

        # ---------- EXIT CHECK ----------
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
                trades.append(TradeV2c(
                    entry_ts=pos["entry_ts"],
                    exit_ts=bar["ts"],
                    entry=pos["entry"],
                    exit=exit_price,
                    stop=pos["stop"],
                    target=pos["target"],
                    r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
                    exit_reason=reason,
                    bars_held=i - pos["entry_idx"],
                    score=pos["score"],
                ))
                pos = None

        # ---------- ENTRY CHECK ----------
        if pos is None:
            window = df.iloc[: i + 1]
            sig = _passes_strict_filters(window, symbol)
            if sig is not None:
                entry = sig["entry"]
                stop = entry - atr_mult * sig["atr"]
                if stop < entry:
                    target = entry + rr_ratio * (entry - stop)
                    net_r = _net_r_at_target(entry, stop, target, fee_pct)
                    if net_r >= min_net_r:
                        pos = {
                            "entry": entry,
                            "stop": stop,
                            "target": target,
                            "entry_ts": bar["ts"],
                            "entry_idx": i,
                            "score": sig["score"],
                        }
                    else:
                        rejected_by_fees += 1
            else:
                rejected_by_filters += 1

        i += 1

    # ---------- FORCE CLOSE ----------
    if pos is not None:
        last = df.iloc[-1]
        exit_price = float(last["close"])
        net = (exit_price - pos["entry"]) - fee_pct * (
            exit_price + pos["entry"]
        )
        risk = pos["entry"] - pos["stop"]
        trades.append(TradeV2c(
            entry_ts=pos["entry_ts"],
            exit_ts=last["ts"],
            entry=pos["entry"],
            exit=exit_price,
            stop=pos["stop"],
            target=pos["target"],
            r_multiple=round(net / risk, 3) if risk > 0 else 0.0,
            exit_reason="eod",
            bars_held=len(df) - 1 - pos["entry_idx"],
            score=pos["score"],
        ))

    msg = f"{len(trades)} معامله"
    if rejected_by_fees:
        msg += f" (رد شده: {rejected_by_fees} کارمزد)"
    return V2cResult(
        symbol=symbol,
        trades=trades,
        ok=True,
        message=msg,
        rejected_by_fees=rejected_by_fees,
        rejected_by_filters=rejected_by_filters,
    )


def compute_v2c_metrics(trades: list[TradeV2c]) -> dict:
    if not trades:
        return {
            "total": 0,
            "wins": 0,
            "losses": 0,
            "win_rate": 0.0,
            "avg_r": 0.0,
            "profit_factor": 0.0,
            "max_dd_r": 0.0,
        }
    wins = [t for t in trades if t.r_multiple > 0]
    losses = [t for t in trades if t.r_multiple <= 0]
    avg_r = sum(t.r_multiple for t in trades) / len(trades)
    gw = sum(t.r_multiple for t in wins)
    gl = abs(sum(t.r_multiple for t in losses))
    pf = gw / gl if gl > 0 else 99.99

    equity = [0.0]
    for t in trades:
        equity.append(equity[-1] + t.r_multiple)
    peak, mdd = equity[0], 0.0
    for v in equity:
        if v > peak:
            peak = v
        if peak - v > mdd:
            mdd = peak - v

    return {
        "total": len(trades),
        "wins": len(wins),
        "losses": len(losses),
        "win_rate": round(len(wins) / len(trades) * 100.0, 1),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(pf, 2),
        "max_dd_r": round(mdd, 2),
    }
    