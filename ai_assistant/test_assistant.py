from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import (
    Base, Instrument, PriceBar, PaperSignal, NewsItem, NewsSymbolLink,
)
from ai_assistant.deterministic import answer


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(s, sym, n=80):
    inst = Instrument(symbol=sym, name=sym, market="TSE", is_active=True)
    s.add(inst); s.commit()
    base = datetime(2025, 1, 1)
    p = 1000.0
    for i in range(n):
        p += 2
        s.add(PriceBar(
            instrument_id=inst.id, ts=base + timedelta(days=i),
            open=p, high=p + 5, low=p - 5, close=p,
            volume=1_000_000, value=1, source="t",
        ))
    s.commit()
    return inst


def test_help_empty():
    s = _session()
    try:
        r = answer(s, "")
        assert r["kind"] == "help"
        assert "می‌توانم" in r["answer"] or "کمک" in r["answer"]
    finally:
        s.close()


def test_help_keyword():
    s = _session()
    try:
        r = answer(s, "کمک")
        assert r["kind"] == "help"
    finally:
        s.close()


def test_portfolio_when_empty():
    s = _session()
    try:
        r = answer(s, "پرتفوی من")
        assert r["kind"] == "portfolio"
        assert "هیچ" in r["answer"]
    finally:
        s.close()


def test_paper_when_empty():
    s = _session()
    try:
        r = answer(s, "Paper trading")
        assert r["kind"] == "paper"
        assert "0" in r["answer"] or "صفر" in r["answer"]
    finally:
        s.close()


def test_symbol_unknown():
    s = _session()
    try:
        r = answer(s, "فلان نماد")
        assert r["kind"] in ("fallback", "symbol")
    finally:
        s.close()


def test_symbol_known():
    s = _session()
    try:
        _seed(s, "فولاد")
        r = answer(s, "تحلیل فولاد")
        assert r["kind"] == "symbol"
        assert "فولاد" in r["answer"]
    finally:
        s.close()


def test_news_negative():
    s = _session()
    try:
        _seed(s, "A")
        item = NewsItem(
            title="تحریم جدید A", sentiment_score=-0.8, impact="high",
            category="political", hash="h1",
        )
        s.add(item); s.commit()
        s.add(NewsSymbolLink(news_id=item.id, symbol="A")); s.commit()
        r = answer(s, "اخبار")
        assert r["kind"] == "news"
        assert "A" in r["answer"]
    finally:
        s.close()


def test_strategy_help():
    s = _session()
    try:
        r = answer(s, "استراتژی v2b چیست")
        assert r["kind"] == "strategy"
        assert "EMA20" in r["answer"]
    finally:
        s.close()


def test_data_coverage():
    s = _session()
    try:
        _seed(s, "X")
        r = answer(s, "وضعیت داده")
        assert r["kind"] == "data"
        assert "1" in r["answer"]
    finally:
        s.close()