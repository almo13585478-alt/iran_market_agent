from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base, Instrument, PriceBar, PaperSignal, MarketSnapshot, AuditLog,
)
from database.audit import log_event
from reports.benchmark import take_snapshot, get_history, paper_equity_curve
from reports.daily_report import build_daily_report, to_markdown


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(s, sym, n=80, up=True):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    p = 1000.0
    for i in range(n):
        p += 2 if up else -2
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p, high=p + 5, low=p - 5, close=p,
            volume=1_000_000, value=1, source="t",
        ))
    s.commit()
    return inst


def test_audit_log_writes():
    s = _session()
    try:
        log_event(s, "test", "hello", subject="X", details="ok")
        assert s.query(AuditLog).count() == 1
    finally:
        s.close()


def test_take_snapshot_creates_row():
    s = _session()
    try:
        _seed(s, "AAA")
        snap = take_snapshot(s)
        assert snap.eq_index > 0
        assert snap.n_symbols == 1
        history = get_history(s)
        assert len(history) == 1
    finally:
        s.close()


def test_paper_equity_curve():
    s = _session()
    try:
        inst = _seed(s, "BBB")
        base = datetime(2025, 1, 1)
        s.add(PaperSignal(
            instrument_id=inst.id, symbol="BBB", strategy="v2b",
            signal_ts=base, entry_price=100, stop_loss=90, target=120,
            status="target_hit", exit_ts=base + timedelta(days=1),
            exit_price=120, r_multiple=2.0,
        ))
        s.add(PaperSignal(
            instrument_id=inst.id, symbol="BBB", strategy="v2b",
            signal_ts=base, entry_price=100, stop_loss=90, target=120,
            status="stop_hit", exit_ts=base + timedelta(days=2),
            exit_price=90, r_multiple=-1.0,
        ))
        s.commit()
        curve = paper_equity_curve(s)
        assert len(curve) == 2
        assert curve[-1][1] == 1.0  # 2 - 1
    finally:
        s.close()


def test_daily_report_structure():
    s = _session()
    try:
        _seed(s, "CCC")
        take_snapshot(s)
        rpt = build_daily_report(s)
        assert rpt.trade_date != ""
        assert rpt.n_active_instruments == 1
        md = to_markdown(rpt)
        assert "گزارش روزانه" in md
        assert "وضعیت بازار" in md
    finally:
        s.close()