from datetime import datetime, timedelta

import pandas as pd
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar
from backtesting.group_backtest import run_group_backtest
from backtesting.strategy_compare import run_grid_search, rank_combos


def _setup_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(session, symbol: str, n: int = 200, up: bool = True):
    inst = Instrument(symbol=symbol, name=symbol, market="TSE", is_active=True)
    session.add(inst)
    session.commit()
    base = datetime(2025, 1, 1)
    p = 1000.0
    for i in range(n):
        p += 2.0 if up else -2.0
        session.add(PriceBar(
            instrument_id=inst.id,
            ts=base + timedelta(days=i),
            open=p, high=p + 8, low=p - 8, close=p,
            volume=2_000_000, value=2_000_000_000, source="test",
        ))
    session.commit()


def test_group_backtest_handles_missing_symbols():
    s = _setup_session()
    try:
        _seed(s, "AAA", 200, up=True)
        r = run_group_backtest(s, ["AAA", "NOPE"], threshold=50.0)
        assert r.symbols_tested == 2
        assert r.symbols_ok >= 1
        assert r.symbols_failed >= 1
    finally:
        s.close()


def test_group_backtest_aggregates():
    s = _setup_session()
    try:
        _seed(s, "AAA", 200, up=True)
        _seed(s, "BBB", 200, up=True)
        r = run_group_backtest(s, ["AAA", "BBB"], threshold=40.0)
        assert r.symbols_ok == 2
        assert r.total_trades >= 0
    finally:
        s.close()


def test_grid_search_returns_all_combos():
    s = _setup_session()
    try:
        _seed(s, "AAA", 150, up=True)
        cmp = run_grid_search(
            s, ["AAA"],
            thresholds=[55.0],
            atr_mults=[2.0],
            rr_ratios=[2.0],
        )
        assert len(cmp.combos) == 1
    finally:
        s.close()


def test_rank_combos_prefers_enough_trades():
    s = _setup_session()
    try:
        _seed(s, "AAA", 250, up=True)
        cmp = run_grid_search(
            s, ["AAA"],
            thresholds=[40.0, 80.0],
            atr_mults=[2.0],
            rr_ratios=[2.0],
        )
        ranked = rank_combos(cmp, min_trades=1)
        assert len(ranked) == 2
        # First result must have >= min_trades
        assert ranked[0].result.total_trades >= 1
    finally:
        s.close()