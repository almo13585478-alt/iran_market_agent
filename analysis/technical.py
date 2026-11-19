"""
High-level technical analysis over a DataFrame of OHLCV bars.
Returns a dict with numeric indicators plus a 0-100 technical score
and human-readable signals. Never returns "buy/sell" labels by itself.
"""

from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd
from loguru import logger

from analysis import indicators as ind


MIN_BARS = 60  # Need enough history for EMA200-ish context


def analyze_prices(df: pd.DataFrame) -> dict[str, Any]:
    """
    df columns: ts (datetime), open, high, low, close, volume, value.
    Returns:
        {
            "ok": bool,
            "message": str,
            "trend": "up"|"down"|"neutral",
            "last_close": float,
            "ema20", "ema50", "ema200": float|None,
            "rsi14": float|None,
            "macd": float|None, "macd_signal": float|None, "macd_hist": float|None,
            "atr14": float|None, "atr_pct": float|None,
            "bb_upper","bb_mid","bb_lower": float|None,
            "adx14": float|None,
            "vol_ma20": float|None,
            "support": float|None, "resistance": float|None,
            "score": float (0-100),
            "signals": list[str],
            "computed_at": datetime,
        }
    """
    if df is None or df.empty:
        return {"ok": False, "message": "داده‌ای برای تحلیل نیست"}
    if len(df) < MIN_BARS:
        return {
            "ok": False,
            "message": f"داده کافی نیست (نیاز: {MIN_BARS}، موجود: {len(df)})",
        }

    df = df.sort_values("ts").reset_index(drop=True)
    close = df["close"].astype(float)
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    volume = df["volume"].astype(float).fillna(0.0)

    ema20 = ind.ema(close, 20)
    ema50 = ind.ema(close, 50)
    ema200 = ind.ema(close, 200) if len(close) >= 200 else pd.Series([np.nan] * len(close))

    rsi14 = ind.rsi(close, 14)
    macd_line, macd_sig, macd_hist = ind.macd(close)
    atr14 = ind.atr(high, low, close, 14)
    bb_upper, bb_mid, bb_lower = ind.bollinger(close)
    adx14 = ind.adx(high, low, close, 14)
    vol_ma20 = ind.sma(volume, 20)

    last_close = float(close.iloc[-1])
    last_ema20 = float(ema20.iloc[-1]) if not pd.isna(ema20.iloc[-1]) else None
    last_ema50 = float(ema50.iloc[-1]) if not pd.isna(ema50.iloc[-1]) else None
    last_ema200 = float(ema200.iloc[-1]) if not pd.isna(ema200.iloc[-1]) else None
    last_rsi = float(rsi14.iloc[-1])
    last_macd = float(macd_line.iloc[-1])
    last_macd_sig = float(macd_sig.iloc[-1])
    last_macd_hist = float(macd_hist.iloc[-1])
    last_atr = float(atr14.iloc[-1]) if not pd.isna(atr14.iloc[-1]) else None
    last_bb_u = float(bb_upper.iloc[-1]) if not pd.isna(bb_upper.iloc[-1]) else None
    last_bb_m = float(bb_mid.iloc[-1]) if not pd.isna(bb_mid.iloc[-1]) else None
    last_bb_l = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else None
    last_adx = float(adx14.iloc[-1]) if not pd.isna(adx14.iloc[-1]) else None
    last_vol_ma = float(vol_ma20.iloc[-1]) if not pd.isna(vol_ma20.iloc[-1]) else None

    # --- Trend detection ---
    trend = "neutral"
    if last_ema20 and last_ema50 and last_ema20 > last_ema50 and last_close > last_ema20:
        trend = "up"
    elif last_ema20 and last_ema50 and last_ema20 < last_ema50 and last_close < last_ema20:
        trend = "down"

    # --- Support / resistance: recent swing highs/lows (30 bars) ---
    window = min(30, len(df))
    recent_high = float(high.tail(window).max())
    recent_low = float(low.tail(window).min())
    support = round(recent_low, 4)
    resistance = round(recent_high, 4)

    # --- Signals ---
    signals: list[str] = []
    if trend == "up":
        signals.append("روند صعودی (EMA20 > EMA50 و قیمت بالای EMA20)")
    elif trend == "down":
        signals.append("روند نزولی (EMA20 < EMA50 و قیمت زیر EMA20)")

    if last_rsi < 30:
        signals.append("RSI در محدوده اشباع فروش (کمتر از 30)")
    elif last_rsi > 70:
        signals.append("RSI در محدوده اشباع خرید (بیشتر از 70)")

    if last_macd > last_macd_sig:
        signals.append("MACD بالای خط سیگنال (مومنتوم مثبت)")
    else:
        signals.append("MACD زیر خط سیگنال (مومنتوم منفی)")

    if last_bb_u and last_close > last_bb_u:
        signals.append("قیمت بالای باند بالایی بولینگر")
    if last_bb_l and last_close < last_bb_l:
        signals.append("قیمت زیر باند پایینی بولینگر")

    if last_vol_ma and volume.iloc[-1] > 1.5 * last_vol_ma:
        signals.append("حجم معاملات امروز ۵۰٪ بیشتر از میانگین ۲۰ روزه")
    elif last_vol_ma and volume.iloc[-1] < 0.5 * last_vol_ma:
        signals.append("حجم معاملات امروز کمتر از نصف میانگین ۲۰ روزه")

    # --- Score 0-100 ---
    score = 50.0
    # Trend component (max ±20)
    if trend == "up":
        score += 15.0
    elif trend == "down":
        score -= 15.0
    # Distance from EMA50 (mean reversion / momentum)
    if last_ema50:
        pct = (last_close - last_ema50) / last_ema50 * 100.0
        if -3.0 <= pct <= 3.0:
            score += 5.0  # near EMA50
        elif 3.0 < pct <= 10.0:
            score += 8.0  # healthy uptrend
        elif pct > 15.0:
            score -= 5.0  # overextended
    # RSI component
    if 40 <= last_rsi <= 60:
        score += 8.0
    elif last_rsi < 30:
        score += 5.0  # oversold — potential bounce
    elif last_rsi > 75:
        score -= 8.0  # overbought
    # MACD momentum
    if last_macd_hist > 0 and last_macd > last_macd_sig:
        score += 7.0
    elif last_macd_hist < 0:
        score -= 5.0
    # ADX trend strength
    if last_adx and last_adx > 25:
        score += 5.0
    elif last_adx and last_adx < 15:
        score -= 3.0
    # Volume confirmation
    if last_vol_ma and volume.iloc[-1] > 1.2 * last_vol_ma and trend == "up":
        score += 5.0
    elif last_vol_ma and volume.iloc[-1] < 0.5 * last_vol_ma:
        score -= 3.0

    score = float(np.clip(score, 0.0, 100.0))

    atr_pct = (last_atr / last_close * 100.0) if last_atr and last_close else None

    return {
        "ok": True,
        "message": "تحلیل انجام شد",
        "trend": trend,
        "last_close": last_close,
        "ema20": last_ema20,
        "ema50": last_ema50,
        "ema200": last_ema200,
        "rsi14": last_rsi,
        "macd": last_macd,
        "macd_signal": last_macd_sig,
        "macd_hist": last_macd_hist,
        "atr14": last_atr,
        "atr_pct": atr_pct,
        "bb_upper": last_bb_u,
        "bb_mid": last_bb_m,
        "bb_lower": last_bb_l,
        "adx14": last_adx,
        "vol_ma20": last_vol_ma,
        "support": support,
        "resistance": resistance,
        "score": score,
        "signals": signals,
        "computed_at": datetime.now(),
    }


def analyze_instrument(session, symbol: str) -> dict[str, Any]:
    """Load bars for symbol from DB and run analyze_prices()."""
    import pandas as pd

    from database.models import Instrument, PriceBar

    inst = session.query(Instrument).filter_by(symbol=symbol).first()
    if inst is None:
        return {"ok": False, "message": f"نماد {symbol} در دیتابیس نیست"}

    bars = (
        session.query(PriceBar)
        .filter_by(instrument_id=inst.id)
        .order_by(PriceBar.ts)
        .all()
    )
    if not bars:
        return {"ok": False, "message": f"برای {symbol} قیمتی ذخیره نشده"}

    df = pd.DataFrame(
        [
            {
                "ts": b.ts,
                "open": b.open,
                "high": b.high,
                "low": b.low,
                "close": b.close,
                "volume": b.volume,
                "value": b.value,
            }
            for b in bars
        ]
    )
    return analyze_prices(df)