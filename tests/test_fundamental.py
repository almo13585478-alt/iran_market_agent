from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, Fundamental
from fundamental.importer import add_fundamental, import_csv
from fundamental.query import get_latest, get_latest_bulk, coverage
from decision_engine.scoring import compute_score


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(s, sym):
    s.add(Instrument(symbol=sym, name=sym, market="TSE", is_active=True))
    s.commit()


def test_add_and_get_latest():
    s = _session()
    try:
        _seed(s, "فولاد")
        ok, _ = add_fundamental(
            s, "فولاد", datetime(2025, 6, 30),
            pe=6.5, roe=25.0, profit_growth=15.0,
        )
        assert ok
        rec = get_latest(s, "فولاد")
        assert rec is not None
        assert rec["pe"] == 6.5
        assert rec["roe"] == 25.0
    finally:
        s.close()


def test_add_overwrites_same_date():
    s = _session()
    try:
        _seed(s, "A")
        add_fundamental(s, "A", datetime(2025, 6, 30), pe=10.0)
        add_fundamental(s, "A", datetime(2025, 6, 30), pe=8.0)
        rec = get_latest(s, "A")
        assert rec["pe"] == 8.0
    finally:
        s.close()


def test_csv_import():
    s = _session()
    try:
        _seed(s, "A")
        _seed(s, "B")
        csv_text = (
            "symbol,as_of_date,pe,roe\n"
            "A,2025-06-30,6.5,25\n"
            "B,2025-06-30,12.0,18\n"
            "MISSING,2025-06-30,10,10\n"
        )
        res = import_csv(s, csv_text)
        assert res["added"] == 2
        assert res["skipped"] >= 1
    finally:
        s.close()


def test_coverage():
    s = _session()
    try:
        _seed(s, "A")
        _seed(s, "B")
        add_fundamental(s, "A", datetime(2025, 6, 30), pe=8.0)
        cov = coverage(s)
        assert cov["total_active"] == 2
        assert cov["with_fundamental"] == 1
        assert cov["pct_covered"] == 50.0
    finally:
        s.close()


def test_fundamental_improves_score():
    s = _session()
    try:
        _seed(s, "A")
        tech = {
            "ok": True, "trend": "up", "rsi14": 55, "adx14": 25,
            "macd_hist": 0.5, "macd": 1.0, "macd_signal": 0.8,
            "atr_pct": 2.0, "vol_ma20": 500_000, "last_close": 1000,
            "ema50": 980, "signals": [], "support": 950,
        }
        good = {"pe": 5.0, "roe": 30.0, "profit_growth": 60.0, "debt_ratio": 25.0}
        bad = {"pe": 60.0, "roe": -10.0, "profit_growth": -30.0, "debt_ratio": 80.0}
        sc_good = compute_score("A", tech, fundamental=good)
        sc_bad = compute_score("A", tech, fundamental=bad)
        assert sc_good.fundamental_score > sc_bad.fundamental_score
    finally:
        s.close()