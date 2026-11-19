"""
Show which v2 filter is blocking entries.

Run: python -m scripts.diagnose_v2_filters [symbol]
Prints pass rate of each filter on the last 60 bars of the symbol.
"""

import sys

import pandas as pd
from sqlalchemy import select

from analysis.indicators import adx, atr, ema, rsi, sma
from analysis.technical import MIN_BARS, analyze_prices
from decision_engine.scoring import compute_score
from database.models import Instrument, PriceBar
from database.session import get_session


def _load_df(session, symbol: str) -> pd.DataFrame | None:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return None
    bars = (
        session.query(PriceBar)
        .filter_by(instrument_id=inst.id)
        .order_by(PriceBar.ts).all()
    )
    if not bars:
        return None
    return pd.DataFrame([
        {"ts": b.ts, "open": b.open, "high": b.high, "low": b.low,
         "close": b.close, "volume": b.volume, "value": b.value}
        for b in bars
    ])


def diagnose(symbol: str):
    s = get_session()
    try:
        df = _load_df(s, symbol)
        if df is None or len(df) < MIN_BARS + 2:
            print(f"{symbol}: داده کافی نیست ({0 if df is None else len(df)} bars)")
            return
        df = df.sort_values("ts").reset_index(drop=True)
        n = len(df)
        print(f"\n=== {symbol} ({n} bars) ===")

        passes = {
            "EMA20 > EMA50": 0,
            "|close-EMA20| <= 3%": 0,
            "candle bullish": 0,
            "ADX >= 20": 0,
            "ADX >= 15 (relaxed)": 0,
            "vol >= 0.7*MA20": 0,
            "RSI in [35,65]": 0,
            "RSI in [30,70] (relaxed)": 0,
            "score >= 55": 0,
            "ALL (strict AND)": 0,
            "6 of 7 (relaxed)": 0,
        }

        for i in range(MIN_BARS, n):
            w = df.iloc[: i + 1]
            close = w["close"].astype(float)
            open_ = w["open"].astype(float)
            volume = w["volume"].astype(float).fillna(0.0)
            high = w["high"].astype(float)
            low = w["low"].astype(float)

            e20 = ema(close, 20).iloc[-1]
            e50 = ema(close, 50).iloc[-1]
            r = rsi(close, 14).iloc[-1]
            a = adx(high, low, close, 14).iloc[-1]
            vma = sma(volume, 20).iloc[-1]
            lc = float(close.iloc[-1])

            if pd.isna(e20) or pd.isna(e50) or pd.isna(r) or pd.isna(a) or pd.isna(vma):
                continue

            ok = {
                "EMA20 > EMA50": e20 > e50,
                "|close-EMA20| <= 3%": -0.03 <= (lc - e20) / e20 <= 0.03,
                "candle bullish": lc > float(open_.iloc[-1]),
                "ADX >= 20": a >= 20,
                "ADX >= 15 (relaxed)": a >= 15,
                "vol >= 0.7*MA20": float(volume.iloc[-1]) >= 0.7 * vma,
                "RSI in [35,65]": 35 <= r <= 65,
                "RSI in [30,70] (relaxed)": 30 <= r <= 70,
            }

            analysis = analyze_prices(w)
            if analysis.get("ok"):
                sc = compute_score(symbol, analysis)
                ok["score >= 55"] = sc.total >= 55

            for k, v in ok.items():
                if v:
                    passes[k] += 1

            if ok["EMA20 > EMA50"] and ok["|close-EMA20| <= 3%"] and \
               ok["candle bullish"] and ok["ADX >= 20"] and \
               ok["vol >= 0.7*MA20"] and ok["RSI in [35,65]"] and \
               ok.get("score >= 55", False):
                passes["ALL (strict AND)"] += 1

            # 6 of 7 (score optional)
            count = sum(1 for k in [
                "EMA20 > EMA50", "|close-EMA20| <= 3%", "candle bullish",
                "ADX >= 15 (relaxed)", "vol >= 0.7*MA20",
                "RSI in [30,70] (relaxed)",
            ] if ok.get(k, False))
            if count >= 5:
                passes["6 of 7 (relaxed)"] += 1

        total = n - MIN_BARS
        print(f"Bars analyzed (after min_bars): {total}\n")
        for k, v in passes.items():
            pct = (v / total * 100) if total else 0
            print(f"  {k:>25}: {v:>4} bars  ({pct:5.1f}%)")
    finally:
        s.close()


if __name__ == "__main__":
    symbol = sys.argv[1] if len(sys.argv) > 1 else "فولاد"
    diagnose(symbol)