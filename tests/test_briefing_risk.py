from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base, Instrument, PriceBar, PortfolioTransaction, MarketSnapshot,
)
from portfolio.risk_advisor import compute_risk_report
from reports.benchmark_comparison import compute_comparison
from ai_assistant.briefing import generate_briefing


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_symbol(s, sym, n=40, price=1000.0, atr_pct=1.0):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    p = price
    for i in range(n):
        delta = p * (atr_pct / 100.0) * ((-1) ** i)
        p = max(1.0, p + delta)
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p, high=p * 1.01, low=p * 0.99, close=p,
            volume=1_000_000, value=1_000_000_000, source="t",
        ))
    s.commit()
    return inst


def test_risk_empty():
    s = _session()
    try:
        rpt = compute_risk_report(s, portfolio_value=1_000_000_000)
        assert rpt.n_positions == 0
        assert "خالی" in rpt.summary
    finally:
        s.close()


def test_risk_concentration():
    s = _session()
    try:
        a = _seed_symbol(s, "A", price=1000)
        b = _seed_symbol(s, "B", price=1000)
        s.add(PortfolioTransaction(
            instrument_id=a.id, side="BUY", price=1000, quantity=8000,
        ))
        s.add(PortfolioTransaction(
            instrument_id=b.id, side="BUY", price=1000, quantity=2000,
        ))
        s.commit()
        rpt = compute_risk_report(s, portfolio_value=10_000_000)
        assert rpt.n_positions == 2
        assert rpt.top_position_pct > 70
        assert rpt.hhi > 5000
        assert any("تمرکز" in w for w in rpt.warnings)
    finally:
        s.close()


def test_risk_var_computed():
    s = _session()
    try:
        a = _seed_symbol(s, "A", price=1000, atr_pct=2.0)
        s.add(PortfolioTransaction(
            instrument_id=a.id, side="BUY", price=1000, quantity=5000,
        ))
        s.commit()
        rpt = compute_risk_report(s, portfolio_value=10_000_000)
        assert rpt.daily_var_95_worst > 0
        assert rpt.daily_var_pct_worst > 0
    finally:
        s.close()


def test_risk_remaining_budget():
    s = _session()
    try:
        a = _seed_symbol(s, "A", price=1000)
        s.add(PortfolioTransaction(
            instrument_id=a.id, side="BUY", price=1000, quantity=1000,
        ))
        s.commit()
        rpt = compute_risk_report(s, portfolio_value=10_000_000,
                                  max_total_risk_pct=1.5)
        assert rpt.remaining_risk_budget_pct >= 0
        assert rpt.remaining_risk_budget_pct <= 1.5
    finally:
        s.close()


def test_benchmark_empty():
    s = _session()
    try:
        rpt = compute_comparison(s)
        assert rpt.portfolio_return_pct is None
        assert "خالی" in rpt.summary
    finally:
        s.close()


def test_benchmark_with_snapshots():
    s = _session()
    try:
        a = _seed_symbol(s, "A", price=1000)
        s.add(PortfolioTransaction(
            instrument_id=a.id, side="BUY", price=1000, quantity=1000,
        ))
        s.commit()

        now = datetime.now()
        s.add(MarketSnapshot(
            trade_date=now - timedelta(days=10),
            state="yellow", state_score=50,
            eq_index=100.0, n_symbols=10,
        ))
        s.add(MarketSnapshot(
            trade_date=now,
            state="yellow", state_score=50,
            eq_index=105.0, n_symbols=10,
        ))
        s.commit()

        rpt = compute_comparison(s)
        assert rpt.index_return_pct == 5.0
        assert rpt.portfolio_return_pct is not None
        assert rpt.alpha_pct is not None
    finally:
        s.close()


def test_briefing_includes_risk():
    s = _session()
    try:
        a = _seed_symbol(s, "A", price=1000)
        s.add(PortfolioTransaction(
            instrument_id=a.id, side="BUY", price=1000, quantity=1000,
        ))
        s.commit()
        b = generate_briefing(s)
        assert b.portfolio["n_positions"] == 1
        assert "summary" in b.risk
        assert isinstance(b.risk["warnings"], list)
        assert b.summary_text != ""
    finally:
        s.close()


def test_briefing_data_health():
    s = _session()
    try:
        _seed_symbol(s, "A", n=40)
        inst = s.query(Instrument).first()
        # Add old position
        s.add(PortfolioTransaction(
            instrument_id=inst.id, side="BUY", price=1000, quantity=100,
        ))
        s.commit()
        b = generate_briefing(s)
        assert "stale_prices" in b.data_health
        assert "stale_fundamentals" in b.data_health
    finally:
        s.close()