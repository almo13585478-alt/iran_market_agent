from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analysis.market_state import classify_market
from database.models import Base, Instrument, PriceBar, PaperSignal
from portfolio.performance import build_report


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_rising(s, sym, n=80, step=2.0):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    p = 1000.0
    for i in range(n):
        p += step
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p, high=p + 5, low=p - 5, close=p,
            volume=1_000_000, value=1, source="t",
        ))
    s.commit()


def test_market_state_requires_data():
    s = _session()
    try:
        st = classify_market(s)
        assert st.state == "red"
        assert st.size_multiplier == 0.5
    finally:
        s.close()


def test_market_state_green_when_rising():
    s = _session()
    try:
        for i in range(15):
            _seed_rising(s, f"S{i}", n=80, step=2.0)
        st = classify_market(s)
        assert st.state in ("green", "yellow")
        assert st.n_symbols > 0
    finally:
        s.close()


def test_performance_empty():
    s = _session()
    try:
        rpt = build_report(s)
        assert rpt.overall.total == 0
        assert rpt.open_count == 0
    finally:
        s.close()


def test_performance_basic():
    s = _session()
    try:
        inst = Instrument(symbol="X", name="X", market="TSE", is_active=True)
        s.add(inst); s.commit()
        base = datetime(2025, 1, 1)
        for st, r in [("target_hit", 2.0), ("stop_hit", -1.0), ("target_hit", 1.5)]:
            s.add(PaperSignal(
                instrument_id=inst.id, symbol="X", strategy="v2b",
                signal_ts=base, entry_price=100, stop_loss=90, target=120,
                status=st, r_multiple=r,
            ))
        s.commit()
        rpt = build_report(s)
        assert rpt.overall.total == 3
        assert rpt.overall.wins == 2
        assert rpt.overall.win_rate == 66.7
        assert "v2b" in rpt.by_strategy
    finally:
        s.close()