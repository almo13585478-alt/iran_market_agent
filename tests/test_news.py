from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, NewsItem, NewsSymbolLink
from news.classifier import classify_news
from news.ingest import add_news, import_csv
from news.query import has_recent_negative_news, negative_symbols_today


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed_inst(s, sym):
    s.add(Instrument(symbol=sym, name=sym, market="TSE", is_active=True))
    s.commit()


def test_classify_negative():
    s = _session()
    try:
        _seed_inst(s, "فولاد")
        cls = classify_news(s, "توقف تولید فولاد به دلیل تحریم جدید")
        assert cls["sentiment_score"] < 0
        assert cls["impact"] == "high"
        assert "فولاد" in cls["symbols"]
    finally:
        s.close()


def test_classify_positive():
    s = _session()
    try:
        _seed_inst(s, "خودرو")
        cls = classify_news(s, "افزایش سرمایه و رشد چشمگیر فروش خودرو")
        assert cls["sentiment_score"] > 0
        assert "خودرو" in cls["symbols"]
    finally:
        s.close()


def test_add_news_dedup():
    s = _session()
    try:
        _seed_inst(s, "فولاد")
        c1, _, _ = add_news(s, "رشد فروش فولاد در ماه گذشته")
        c2, msg, _ = add_news(s, "رشد فروش فولاد در ماه گذشته")
        assert c1 is True
        assert c2 is False
        assert "تکراری" in msg
    finally:
        s.close()


def test_has_recent_negative_news():
    s = _session()
    try:
        _seed_inst(s, "فولاد")
        _seed_inst(s, "خودرو")
        add_news(s, "زیان سنگین فولاد به دلیل توقف تولید")
        assert has_recent_negative_news(s, "فولاد", days=3) is True
        assert has_recent_negative_news(s, "خودرو", days=3) is False
    finally:
        s.close()


def test_csv_import():
    s = _session()
    try:
        _seed_inst(s, "خودرو")
        csv_text = (
            "title,body,source\n"
            "افت فروش خودرو در ماه اخیر,به دلیل کمبود قطعات,csv\n"
            "قرارداد بزرگ صادرات فولاد,,csv\n"
        )
        res = import_csv(s, csv_text)
        assert res["added"] == 2
        assert res["skipped"] == 0
    finally:
        s.close()


def test_negative_symbols_today():
    s = _session()
    try:
        _seed_inst(s, "فولاد")
        _seed_inst(s, "خودرو")
        add_news(s, "زیان سنگین فولاد به دلیل تحریم")
        add_news(s, "افت فروش خودرو در ماه اخیر")
        neg = negative_symbols_today(s, days=3)
        symbols = {n["symbol"] for n in neg}
        assert "فولاد" in symbols
        assert "خودرو" in symbols
    finally:
        s.close()