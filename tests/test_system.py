from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar, PaperSignal
from scripts.system_health import compute_health
from scripts.backup_db import (
    BACKUP_DIR, _row_to_dict, USER_DATA_TABLES,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_priced(s, sym="A", n=100, price=1000.0):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    for i in range(n):
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=price, high=price + 10, low=price - 10, close=price,
            volume=1_000_000, value=1_000_000_000, source="t",
        ))
    s.commit()
    return inst


# ---- Health checks ----

def test_health_empty_db():
    s = _session()
    try:
        rpt = compute_health(s)
        assert rpt.overall_status in ("fail", "warn")
        assert rpt.ready_for_real_money is False
        # Should have at least these items
        names = [i.name for i in rpt.items]
        assert "نمادها" in names
        assert "پوشش قیمت" in names
    finally:
        s.close()


def test_health_with_data():
    s = _session()
    try:
        _seed_priced(s, "A", n=250)
        rpt = compute_health(s)
        # Some items should be ok/warn now
        assert any(i.status in ("ok", "warn") for i in rpt.items)
    finally:
        s.close()


# ---- Readiness ----

def test_readiness_all_checks_present():
    s = _session()
    try:
        rpt = compute_health(s)
        assert len(rpt.readiness) == 10
        # All should be pass/warn/fail
        for r in rpt.readiness:
            assert r.status in ("pass", "warn", "fail")
    finally:
        s.close()


def test_readiness_not_ready_empty():
    s = _session()
    try:
        rpt = compute_health(s)
        assert rpt.ready_for_real_money is False
    finally:
        s.close()


def test_readiness_paper_check():
    s = _session()
    try:
        inst = _seed_priced(s, "A")
        # Add 25 closed signals with good R
        base = datetime(2025, 1, 1)
        for i in range(25):
            s.add(PaperSignal(
                instrument_id=inst.id, symbol="A", strategy="v2b",
                signal_ts=base + timedelta(days=i),
                entry_price=1000, stop_loss=950, target=1100,
                status="target_hit",
                exit_ts=base + timedelta(days=i + 1),
                exit_price=1100, r_multiple=2.0,
            ))
        s.commit()
        rpt = compute_health(s)
        paper_check = next(
            (r for r in rpt.readiness if "Paper" in r.name), None
        )
        assert paper_check is not None
        assert paper_check.status == "pass"
    finally:
        s.close()


# ---- Backup helpers ----

def test_row_to_dict_serialization():
    from datetime import datetime
    from database.models import PortfolioTransaction

    tx = PortfolioTransaction(
        instrument_id=1, side="BUY", price=100.0, quantity=50.0,
        ts=datetime(2025, 6, 30, 12, 30),
    )
    d = _row_to_dict(tx)
    assert d["side"] == "BUY"
    assert d["price"] == 100.0
    # ts should be ISO string
    assert isinstance(d["ts"], str)
    assert "2025-06-30" in d["ts"]


def test_user_data_tables_defined():
    assert len(USER_DATA_TABLES) >= 8
    names = [name for name, _ in USER_DATA_TABLES]
    assert "portfolio_transactions" in names
    assert "paper_signals" in names
    assert "fundamentals" in names
    assert "macro_indicators" in names