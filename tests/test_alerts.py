from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar, PortfolioTransaction
from alerts.engine import check_alerts


def _setup_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_bars(session, inst_id, n=80, close=1000.0, trend="flat"):
    base = datetime(2025, 1, 1)
    price = close
    for i in range(n):
        price += 1.0 if trend == "up" else (-1.0 if trend == "down" else 0.0)
        session.add(PriceBar(
            instrument_id=inst_id,
            ts=base + timedelta(days=i),
            open=price, high=price + 5, low=price - 5, close=price,
            volume=1_000_000, value=1_000_000_000, source="test",
        ))


def test_alerts_empty_when_no_holdings():
    s = _setup_session()
    try:
        assert check_alerts(s) == []
    finally:
        s.close()


def test_alerts_returns_list_for_holding():
    s = _setup_session()
    try:
        inst = Instrument(symbol="TST", name="Test", market="TSE", is_active=True)
        s.add(inst)
        s.commit()
        _seed_bars(s, inst.id, n=80, close=1000.0, trend="down")
        s.add(PortfolioTransaction(
            instrument_id=inst.id, side="BUY",
            price=1100.0, quantity=1000.0, fee=0.0,
        ))
        s.commit()
        alerts = check_alerts(s)
        assert isinstance(alerts, list)
    finally:
        s.close()