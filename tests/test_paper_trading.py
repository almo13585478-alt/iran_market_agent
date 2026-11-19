from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar, PaperSignal
from paper_trading.scanner import detect_v2b_on_last_bar
from paper_trading.tracker import update_open_signals, paper_stats


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def test_tracker_marks_stop_hit():
    s = _session()
    try:
        inst = Instrument(symbol="AAA", name="A", market="TSE", is_active=True)
        s.add(inst)
        s.commit()

        base = datetime(2025, 1, 1)
        # signal on bar 0 at 100, stop 90, target 120
        s.add(PriceBar(
            instrument_id=inst.id, ts=base,
            open=100, high=101, low=99, close=100,
            volume=1_000_000, value=1, source="t",
        ))
        sig = PaperSignal(
            instrument_id=inst.id, symbol="AAA", strategy="v2b",
            signal_ts=base, entry_price=100, stop_loss=90, target=120,
            status="open",
        )
        s.add(sig)
        s.commit()

        # next bar drops through stop
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=1),
            open=100, high=101, low=88, close=95,
            volume=1_000_000, value=1, source="t",
        ))
        s.commit()

        counts = update_open_signals(s)
        s.refresh(sig)
        assert sig.status == "stop_hit"
        assert sig.r_multiple == -1.0
        assert counts["closed_stop"] == 1
    finally:
        s.close()


def test_tracker_marks_target_hit():
    s = _session()
    try:
        inst = Instrument(symbol="BBB", name="B", market="TSE", is_active=True)
        s.add(inst)
        s.commit()

        base = datetime(2025, 1, 1)
        s.add(PriceBar(
            instrument_id=inst.id, ts=base,
            open=100, high=101, low=99, close=100,
            volume=1_000_000, value=1, source="t",
        ))
        sig = PaperSignal(
            instrument_id=inst.id, symbol="BBB", strategy="v2b",
            signal_ts=base, entry_price=100, stop_loss=90, target=120,
            status="open",
        )
        s.add(sig)
        s.commit()

        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=1),
            open=100, high=125, low=99, close=122,
            volume=1_000_000, value=1, source="t",
        ))
        s.commit()

        update_open_signals(s)
        s.refresh(sig)
        assert sig.status == "target_hit"
        assert sig.r_multiple == 2.0
    finally:
        s.close()


def test_stats_basic():
    s = _session()
    try:
        inst = Instrument(symbol="C", name="C", market="TSE", is_active=True)
        s.add(inst)
        s.commit()
        base = datetime(2025, 1, 1)
        for status, r in [("target_hit", 2.0), ("stop_hit", -1.0)]:
            s.add(PaperSignal(
                instrument_id=inst.id, symbol="C", strategy="v2b",
                signal_ts=base, entry_price=100, stop_loss=90, target=120,
                status=status, r_multiple=r,
            ))
        s.commit()
        st = paper_stats(s)
        assert st["total"] == 2
        assert st["wins"] == 1
        assert st["win_rate"] == 50.0
    finally:
        s.close()