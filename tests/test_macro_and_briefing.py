from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, MacroIndicator
from macro.importer import add_indicator, import_csv
from macro.query import latest_all, history, pct_change
from ai_assistant.briefing import generate_briefing


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def test_add_macro():
    s = _session()
    try:
        ok, _ = add_indicator(
            s, "usd_free", "دلار آزاد", 900000.0,
            unit="IRR", as_of_date=datetime(2025, 6, 30),
        )
        assert ok
        latest = latest_all(s)
        assert "usd_free" in latest
        assert latest["usd_free"]["value"] == 900000.0
    finally:
        s.close()


def test_add_macro_overwrites():
    s = _session()
    try:
        d = datetime(2025, 6, 30)
        add_indicator(s, "usd_free", "دلار آزاد", 900000.0, as_of_date=d)
        add_indicator(s, "usd_free", "دلار آزاد", 950000.0, as_of_date=d)
        latest = latest_all(s)
        assert latest["usd_free"]["value"] == 950000.0
        assert s.query(MacroIndicator).count() == 1
    finally:
        s.close()


def test_csv_import():
    s = _session()
    try:
        csv_text = (
            "key,label,value,unit,as_of_date,source\n"
            "usd_free,دلار آزاد,900000,IRR,2025-06-30,manual\n"
            "inflation_yoy,تورم,35.5,%,2025-06-01,cbi\n"
        )
        res = import_csv(s, csv_text)
        assert res["added"] == 2
        assert s.query(MacroIndicator).count() == 2
    finally:
        s.close()


def test_pct_change():
    s = _session()
    try:
        now = datetime.now()
        add_indicator(s, "usd_free", "دلار", 1000.0,
                      as_of_date=now - timedelta(days=20))
        add_indicator(s, "usd_free", "دلار", 1100.0,
                      as_of_date=now - timedelta(days=10))
        ch = pct_change(s, "usd_free", days=30)
        assert ch == 10.0
    finally:
        s.close()


def test_pct_change_insufficient():
    s = _session()
    try:
        add_indicator(s, "x", "X", 100.0)
        ch = pct_change(s, "x", days=30)
        assert ch is None
    finally:
        s.close()


def test_history():
    s = _session()
    try:
        now = datetime.now()
        for i in range(5):
            add_indicator(s, "x", "X", float(100 + i),
                          as_of_date=now - timedelta(days=4 - i))
        h = history(s, "x", days=30)
        assert len(h) == 5
        assert h[0]["value"] == 100.0
        assert h[-1]["value"] == 104.0
    finally:
        s.close()


def test_briefing_empty():
    s = _session()
    try:
        b = generate_briefing(s)
        assert b.summary_text != ""
        assert isinstance(b.action_items, list)
        assert len(b.action_items) >= 1
    finally:
        s.close()


def test_briefing_with_macro():
    s = _session()
    try:
        now = datetime.now()
        add_indicator(s, "usd_free", "دلار آزاد", 1000.0,
                      as_of_date=now - timedelta(days=20))
        add_indicator(s, "usd_free", "دلار آزاد", 1100.0,
                      as_of_date=now)
        b = generate_briefing(s)
        assert b.macro.get("usd_30d_pct") is not None
        assert b.macro["usd_30d_pct"] == 10.0
    finally:
        s.close()