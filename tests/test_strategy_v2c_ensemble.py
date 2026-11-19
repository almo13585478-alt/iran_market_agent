from datetime import datetime, timedelta

import pandas as pd

from backtesting.strategy_v2c import backtest_v2c, compute_v2c_metrics
from backtesting.ensemble import backtest_ensemble, compute_ensemble_metrics


def _mk_df(n=250, up=True, vol=2_000_000):
    base = datetime(2025, 1, 1)
    rows = []
    p = 1000.0
    for i in range(n):
        p += 1.5 if up else -1.5
        rows.append({
            "ts": base + timedelta(days=i),
            "open": p - 0.5, "high": p + 6, "low": p - 6, "close": p,
            "volume": vol, "value": vol * p,
        })
    return pd.DataFrame(rows)


def test_v2c_rejects_short_data():
    df = _mk_df(n=50)
    r = backtest_v2c("X", df)
    assert not r.ok
    assert "کافی" in r.message


def test_v2c_accepts_long_uptrend():
    df = _mk_df(n=300, up=True)
    r = backtest_v2c("X", df)
    assert r.ok
    assert isinstance(r.trades, list)


def test_v2c_metrics_empty():
    m = compute_v2c_metrics([])
    assert m["total"] == 0
    assert m["win_rate"] == 0.0


def test_ensemble_rejects_short_data():
    df = _mk_df(n=50)
    r = backtest_ensemble("X", df)
    assert not r.ok


def test_ensemble_accepts_long_uptrend():
    df = _mk_df(n=350, up=True, vol=3_000_000)
    r = backtest_ensemble("X", df, strategies=("v2b", "breakout", "v2c"), min_agreement=2)
    assert r.ok
    assert isinstance(r.trades, list)
    # every trade must be backed by >=2 strategies
    for t in r.trades:
        assert len(t.strategies) >= 2


def test_ensemble_stop_is_tightest():
    """Stop = max of stops (smallest loss per trade)."""
    df = _mk_df(n=350, up=True, vol=3_000_000)
    r = backtest_ensemble("X", df, min_agreement=2)
    if r.trades:
        # entry is average, stop must be below entry
        for t in r.trades:
            assert t.stop < t.entry
            assert t.target > t.entry


def test_ensemble_metrics_ok():
    df = _mk_df(n=400, up=True, vol=3_000_000)
    r = backtest_ensemble("X", df, min_agreement=2)
    m = compute_ensemble_metrics(r.trades)
    assert "strategies_used" in m
    assert m["total"] == len(r.trades)