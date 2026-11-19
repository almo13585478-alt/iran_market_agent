from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.models import Base, Instrument, PaperSignal
from paper_trading.analytics import (
    compute_strategy_analytics, compute_weekly_buckets,
    compute_drawdown_series, signal_quality_vs_outcome, all_strategies,
)


def _session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    return S()


def _seed(s):
    s.add(Instrument(symbol="X", name="X", market="TSE", is_active=True))
    s.commit()
    return s.query(Instrument).first()


def _sig(inst, ts, r, status="target_hit", strategy="v2b", score=70.0):
    return PaperSignal(
        instrument_id=inst.id, symbol="X", strategy=strategy,
        signal_ts=ts, entry_price=100, stop_loss=90, target=120,
        status=status, exit_ts=ts, exit_price=100 + r * 10, r_multiple=r,
        score_at_signal=score,
    )


def _recent(days_ago: int) -> datetime:
    """Return a datetime N days ago, safely inside the 12-week window."""
    return datetime.now() - timedelta(days=days_ago)


def test_empty_strategy():
    s = _session()
    try:
        _seed(s)
        a = compute_strategy_analytics(s, "v2b")
        assert a.total == 0
    finally:
        s.close()


def test_streak_calculation():
    s = _session()
    try:
        inst = _seed(s)
        for i, r in enumerate([2.0, 1.0, 1.5, -1.0, -0.5, 2.0]):
            s.add(_sig(inst, _recent(60 - i), r))
        s.commit()
        a = compute_strategy_analytics(s, "v2b")
        assert a.max_win_streak == 3
        assert a.max_loss_streak == 2
        assert a.current_streak == 1
        assert a.wins == 4 and a.losses == 2
    finally:
        s.close()


def test_r_bins():
    s = _session()
    try:
        inst = _seed(s)
        for i, r in enumerate([3.0, 1.5, 0.5, -0.5, -1.5, -3.0]):
            s.add(_sig(inst, _recent(60 - i), r))
        s.commit()
        a = compute_strategy_analytics(s, "v2b")
        assert a.r_bins["> +2"] == 1
        assert a.r_bins["+1 to +2"] == 1
        assert a.r_bins["< -2"] == 1
    finally:
        s.close()


def test_weekly_buckets():
    s = _session()
    try:
        inst = _seed(s)
        # Recent 3 days — guaranteed inside 12-week window
        for i in range(3):
            s.add(_sig(inst, _recent(2 - i), 1.0))
        s.commit()
        weeks = compute_weekly_buckets(s, None, weeks=12)
        assert len(weeks) >= 1
        assert sum(w.total for w in weeks) == 3
    finally:
        s.close()


def test_drawdown():
    s = _session()
    try:
        inst = _seed(s)
        for i, r in enumerate([2.0, 2.0, -3.0, 1.0]):
            s.add(_sig(inst, _recent(30 - i), r))
        s.commit()
        dd = compute_drawdown_series(s, None)
        assert len(dd) == 4
        assert max(d.drawdown_r for d in dd) >= 3.0
    finally:
        s.close()


def test_signal_quality_correlation():
    s = _session()
    try:
        inst = _seed(s)
        for i in range(6):
            s.add(_sig(inst, _recent(30 - i), r=float(i), score=50.0 + i * 5))
        s.commit()
        q = signal_quality_vs_outcome(s)
        assert q["correlation"] is not None
        assert q["correlation"] > 0.5
    finally:
        s.close()


def test_all_strategies():
    s = _session()
    try:
        inst = _seed(s)
        s.add(_sig(inst, _recent(2), 1.0, strategy="v2b"))
        s.add(_sig(inst, _recent(1), 1.0, strategy="breakout"))
        s.commit()
        assert all_strategies(s) == ["breakout", "v2b"]
    finally:
        s.close()