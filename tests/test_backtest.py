from datetime import datetime, timedelta

import pandas as pd
import pytest

from backtesting.simple_backtest import backtest_symbol, compute_metrics


def _mk_df(n=120, close=1000.0, trend="up"):
    base = datetime(2025, 1, 1)
    rows = []
    p = close
    for i in range(n):
        p += 2.0 if trend == "up" else (-2.0 if trend == "down" else 0.0)
        rows.append({
            "ts": base + timedelta(days=i),
            "open": p, "high": p + 6, "low": p - 6, "close": p,
            "volume": 1_500_000, "value": 1_500_000_000,
        })
    return pd.DataFrame(rows)


def test_backtest_rejects_short_data():
    df = _mk_df(n=30)
    r = backtest_symbol("TEST", df)
    assert not r.ok
    assert "داده کافی نیست" in r.message


def test_backtest_produces_result_structure():
    df = _mk_df(n=120, trend="up")
    r = backtest_symbol("TEST", df, threshold=50.0)
    assert r.ok
    assert isinstance(r.trades, list)
    for t in r.trades:
        assert t.entry > 0
        assert t.stop < t.entry
        assert t.target > t.entry
        assert t.exit_reason in ("stop", "target", "eod")


def test_backtest_empty_metrics():
    m = compute_metrics([])
    assert m["total"] == 0
    assert m["win_rate"] == 0.0


def test_backtest_metrics_basic():
    df = _mk_df(n=200, trend="up")
    r = backtest_symbol("TEST", df, threshold=40.0)
    m = compute_metrics(r.trades)
    assert m["total"] == len(r.trades)
    assert 0.0 <= m["win_rate"] <= 100.0
    assert m["profit_factor"] >= 0.0