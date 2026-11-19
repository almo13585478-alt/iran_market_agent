import csv
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PriceBar
from data_sources.csv_importer import CsvImporterAdapter
from data_engine.sync_engine import sync_instruments, sync_prices


@pytest.fixture
def session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def csv_dir(tmp_path: Path):
    d = tmp_path / "imports"
    (d / "prices").mkdir(parents=True)

    with open(d / "instruments.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "name", "market", "industry", "ins_code"])
        w.writerow(["فولاد", "فولاد مبارکه", "TSE", "فلزات اساسی", "12345"])
        w.writerow(["خودرو", "ایران خودرو", "TSE", "خودرو", ""])

    with open(d / "prices" / "فولاد.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["date", "open", "high", "low", "close", "volume", "value"])
        w.writerow(["2025-01-01", "1000", "1100", "950", "1080", "1000000", "1080000000"])
        w.writerow(["2025-01-02", "1080", "1120", "1050", "1100", "900000", "990000000"])

    return d


def test_csv_importer_health(csv_dir):
    a = CsvImporterAdapter(base_dir=csv_dir)
    r = a.health_check()
    assert r.ok


def test_sync_instruments_from_csv(csv_dir, session):
    a = CsvImporterAdapter(base_dir=csv_dir)
    r = sync_instruments(a, session)
    assert r.ok
    assert r.added == 2

    symbols = {i.symbol for i in session.scalars(select(Instrument)).all()}
    assert symbols == {"فولاد", "خودرو"}


def test_sync_prices_from_csv(csv_dir, session):
    a = CsvImporterAdapter(base_dir=csv_dir)
    sync_instruments(a, session)

    r = sync_prices(a, session, "فولاد", days=30)
    assert r.ok
    assert r.added == 2

    bars = session.scalars(select(PriceBar)).all()
    assert len(bars) == 2
    assert bars[-1].close == 1100.0