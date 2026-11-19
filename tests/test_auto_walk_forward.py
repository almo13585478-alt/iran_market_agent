from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar, StrategyConfig
from backtesting.auto_walk_forward import (
    get_active, get_history, run_auto_walk_forward, accept_manually,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_uptrend(s, sym, n=300, price=1000.0):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    p = price
    for i in range(n):
        p *= 1.003
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p * 0.99, high=p * 1.02, low=p * 0.98, close=p,
            volume=2_000_000, value=2_000_000_000, source="t",
        ))
    s.commit()
    return inst


def test_empty_symbols():
    s = _session()
    try:
        r = run_auto_walk_forward(s, strategy="v2b", n_symbols=10)
        assert r.ok is False
        assert "نمادی" in r.message
    finally:
        s.close()


def test_run_persists_results():
    s = _session()
    try:
        _seed_uptrend(s, "A", n=300)
        _seed_uptrend(s, "B", n=300)
        _seed_uptrend(s, "C", n=300)

        r = run_auto_walk_forward(
            s, strategy="v2b",
            thresholds=[60.0],
            atr_mults=[2.0],
            rr_ratios=[2.0],
            n_symbols=10,
        )
        assert r.ok
        assert r.n_combos == 1
        configs = s.query(StrategyConfig).all()
        assert len(configs) == 1
    finally:
        s.close()


def test_active_set_if_robust():
    s = _session()
    try:
        for i in range(5):
            _seed_uptrend(s, f"S{i}", n=300)

        r = run_auto_walk_forward(
            s, strategy="v2b",
            thresholds=[55.0],
            atr_mults=[2.0],
            rr_ratios=[2.0],
            n_symbols=10,
        )
        assert r.ok
        active = get_active(s, "v2b")
        # If any robust found, active should be set
        if r.n_robust > 0:
            assert active is not None
            assert active.is_active is True
    finally:
        s.close()


def test_no_swap_when_new_worse():
    """If new run has worse test_avg_r than current, no swap."""
    s = _session()
    try:
        _seed_uptrend(s, "A", n=300)
        _seed_uptrend(s, "B", n=300)

        # Manually insert an active config with high test_avg_r
        s.add(StrategyConfig(
            strategy="v2b",
            threshold=55.0, atr_mult=2.0, rr_ratio=2.0,
            train_avg_r=2.0, test_avg_r=2.0,
            test_win_rate=80.0, test_trades=20,
            verdict="robust", is_active=True,
            applied_at=datetime.now(),
            source="manual",
        ))
        s.commit()

        # Run with params likely producing lower avgR
        r = run_auto_walk_forward(
            s, strategy="v2b",
            thresholds=[75.0],
            atr_mults=[2.5],
            rr_ratios=[2.5],
            n_symbols=10,
        )
        assert r.ok
        # The artificially high active should still be active
        active = get_active(s, "v2b")
        assert active is not None
        assert active.test_avg_r == 2.0
        # If we found robust in run, it should be worse
        if r.n_robust > 0:
            assert r.swapped is False
            assert r.warning is not None
    finally:
        s.close()


def test_manual_accept():
    s = _session()
    try:
        # Create two configs
        c1 = StrategyConfig(
            strategy="v2b", threshold=55, atr_mult=2.0, rr_ratio=2.0,
            verdict="robust", is_active=True, test_avg_r=0.5,
            applied_at=datetime.now(),
        )
        c2 = StrategyConfig(
            strategy="v2b", threshold=65, atr_mult=2.5, rr_ratio=2.5,
            verdict="robust", is_active=False, test_avg_r=0.8,
        )
        s.add_all([c1, c2])
        s.commit()

        ok, msg = accept_manually(s, c2.id)
        assert ok
        # c1 should be inactive, c2 active
        active = get_active(s, "v2b")
        assert active.id == c2.id
        c1_db = s.query(StrategyConfig).filter_by(id=c1.id).first()
        assert c1_db.is_active is False
    finally:
        s.close()


def test_history():
    s = _session()
    try:
        for i in range(5):
            s.add(StrategyConfig(
                strategy="v2b", threshold=55 + i, atr_mult=2.0,
                rr_ratio=2.0, verdict="robust",
                computed_at=datetime.now() - timedelta(hours=i),
            ))
        s.commit()
        hist = get_history(s, "v2b", limit=10)
        assert len(hist) == 5
        # Sorted desc by computed_at
        assert hist[0].threshold == 55
    finally:
        s.close()