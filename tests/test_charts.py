from datetime import datetime, timedelta

import pandas as pd

from dashboard.chart_helpers import (
    candlestick_with_setup, sparkline, market_gauge,
    industry_treemap, equity_curve,
)
from analysis.industry import IndustryStat


def _mk_df(n=100, start=1000.0, step=2.0):
    base = datetime(2025, 1, 1)
    rows = []
    p = start
    for i in range(n):
        p += step
        rows.append({
            "ts": base + timedelta(days=i),
            "open": p, "high": p + 5, "low": p - 5, "close": p,
            "volume": 1_000_000, "value": 1_000_000_000,
        })
    return pd.DataFrame(rows)


def test_candlestick_basic():
    fig = candlestick_with_setup(_mk_df(), symbol="TEST")
    assert fig is not None
    assert len(fig.data) >= 3  # candles + EMAs


def test_candlestick_empty():
    assert candlestick_with_setup(pd.DataFrame()) is None


def test_candlestick_short():
    assert candlestick_with_setup(_mk_df(n=10)) is None


def test_candlestick_with_setup_lines():
    fig = candlestick_with_setup(
        _mk_df(), symbol="T",
        entry=1100, entry_high=1110, stop=1050,
        target1=1200, target2=1250,
    )
    assert fig is not None
    # At least 3 hlines added (stop, target1, target2) + entry rect
    assert len(fig.layout.shapes) >= 4


def test_sparkline():
    fig = sparkline(_mk_df(30))
    assert fig is not None


def test_sparkline_empty():
    assert sparkline(pd.DataFrame()) is None


def test_market_gauge():
    for state in ("green", "yellow", "red"):
        fig = market_gauge(state, 50.0)
        assert fig is not None


def test_industry_treemap():
    stats = [
        IndustryStat(industry="A", n_symbols=10, avg_score=75.0),
        IndustryStat(industry="B", n_symbols=5, avg_score=50.0),
    ]
    fig = industry_treemap(stats)
    assert fig is not None


def test_industry_treemap_empty():
    assert industry_treemap([]) is None


def test_equity_curve():
    pts = [
        (datetime(2025, 1, 1), 1.0),
        (datetime(2025, 1, 2), 2.0),
        (datetime(2025, 1, 3), 0.5),
    ]
    fig = equity_curve(pts)
    assert fig is not None


def test_equity_curve_empty():
    assert equity_curve([]) is None
def test_market_heatmap():
    from dashboard.chart_helpers import market_heatmap
    data = [
        {"symbol": "A", "pct_change": 2.5, "score": 80, "close": 1000},
        {"symbol": "B", "pct_change": -1.2, "score": 45, "close": 2000},
        {"symbol": "C", "pct_change": 0.0, "score": 60, "close": 1500},
    ]
    fig = market_heatmap(data)
    assert fig is not None


def test_market_heatmap_empty():
    from dashboard.chart_helpers import market_heatmap
    assert market_heatmap([]) is None