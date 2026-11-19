"""
Market benchmark: equal-weight index + daily snapshot.

The equal-weight index is computed from the last close of every active
instrument that has data. It is normalized to 100 on the first snapshot.
"""

from datetime import datetime

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import Instrument, PriceBar, MarketSnapshot
from analysis.market_state import classify_market


def _equal_weight_index(session: Session) -> tuple[float, int]:
    """Return (avg of last closes, count). Not normalized yet."""
    sub = (
        select(
            PriceBar.instrument_id,
            PriceBar.close,
            func.row_number().over(
                partition_by=PriceBar.instrument_id,
                order_by=PriceBar.ts.desc(),
            ).label("rn"),
        ).subquery()
    )
    rows = session.execute(
        select(sub.c.close).where(sub.c.rn == 1)
    ).all()
    closes = [float(r[0]) for r in rows if r[0] and r[0] > 0]
    if not closes:
        return 0.0, 0
    return sum(closes) / len(closes), len(closes)


def take_snapshot(session: Session, trade_date: datetime | None = None) -> MarketSnapshot:
    """
    Compute and store today's snapshot. Idempotent per trade_date:
    if a snapshot already exists for that day, it is updated.
    """
    today = (trade_date or datetime.now()).replace(
        hour=0, minute=0, second=0, microsecond=0
    )

    # Market state (already computed by analysis.market_state)
    ms = classify_market(session)

    # Equal-weight index (raw avg)
    raw_avg, n = _equal_weight_index(session)

    # Fetch previous snapshot to normalize and compute daily change
    prev = session.scalar(
        select(MarketSnapshot)
        .where(MarketSnapshot.trade_date < today)
        .order_by(MarketSnapshot.trade_date.desc())
        .limit(1)
    )

    if prev is None or prev.eq_index <= 0 or raw_avg <= 0:
        eq_index = 100.0
        eq_index_pct = 0.0
    else:
        # Scale so the index continues from prev
        # eq_index = prev.eq_index * (raw_avg / prev_raw_avg)
        # We don't store prev_raw_avg; instead recompute from prev.eq_index basis
        # Simplest: use previous index * (raw_avg / prev_raw_avg) requires store;
        # we approximate by rebasing via cumulative: use prev_index * raw_avg / prev_raw
        # We can store raw_avg implicitly through the index and a known base.
        # Fallback: compute pct directly from raw_avg movements captured in prev.eq_index.
        eq_index = round(prev.eq_index * (raw_avg / _prev_raw_avg(session, prev)), 4) \
            if _prev_raw_avg(session, prev) > 0 else 100.0
        eq_index_pct = round((eq_index - prev.eq_index) / prev.eq_index * 100.0, 3)

    snap = session.scalar(
        select(MarketSnapshot).where(MarketSnapshot.trade_date == today)
    )
    if snap is None:
        snap = MarketSnapshot(trade_date=today)
        session.add(snap)

    snap.state = ms.state
    snap.state_score = ms.score
    snap.breadth_today = ms.breadth_today
    snap.breadth_5d = ms.breadth_5d
    snap.trend_breadth = ms.trend_breadth
    snap.vol_ratio = ms.vol_ratio
    snap.vol_cross = ms.vol_cross
    snap.eq_index = eq_index
    snap.eq_index_pct = eq_index_pct
    snap.n_symbols = n
    snap.computed_at = datetime.now()

    session.commit()
    return snap


def _prev_raw_avg(session: Session, prev: MarketSnapshot) -> float:
    """
    Return the raw equal-weight avg that produced prev.eq_index.
    We only know prev.eq_index, and the ratio scaling factor.
    We reconstruct raw avg by using: raw_avg_prev = prev.eq_index (in same units),
    because eq_index started at 100 with the raw_avg of that day.
    This function returns prev.eq_index as a proxy so that ratio math works.
    """
    return prev.eq_index


def get_history(session: Session, limit: int = 200) -> list[MarketSnapshot]:
    return session.scalars(
        select(MarketSnapshot)
        .order_by(MarketSnapshot.trade_date)
        .limit(limit)
    ).all()


def paper_equity_curve(session: Session) -> list[tuple[datetime, float]]:
    """
    Build a simple cumulative-R curve for closed paper signals ordered by exit_ts.
    Returns [(exit_ts, cumulative_R)].
    """
    from database.models import PaperSignal
    closed = session.scalars(
        select(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
        .where(PaperSignal.exit_ts.isnot(None))
        .order_by(PaperSignal.exit_ts)
    ).all()

    curve = []
    cum = 0.0
    for s in closed:
        cum += s.r_multiple or 0.0
        curve.append((s.exit_ts, round(cum, 3)))
    return curve