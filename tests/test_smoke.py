from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument


def test_db_init_and_insert():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    s = S()
    try:
        s.add(Instrument(symbol="TEST", name="نماد آزمایشی", market="TSE"))
        s.commit()
        rec = s.scalar(select(Instrument).where(Instrument.symbol == "TEST"))
        assert rec is not None
        assert rec.symbol == "TEST"
    finally:
        s.close()
        engine.dispose()