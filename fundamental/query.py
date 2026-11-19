"""
Fundamental queries: latest per symbol, bulk, coverage stats.
Returns dicts ready for `compute_score(fundamental=...)`.
"""

from sqlalchemy import select, func, distinct
from sqlalchemy.orm import Session

from database.models import Fundamental, Instrument


def _row_to_dict(row: Fundamental) -> dict:
    return {
        "as_of_date": row.as_of_date,
        "pe": row.pe,
        "pb": row.pb,
        "ps": row.ps,
        "eps": row.eps,
        "roe": row.roe,
        "roa": row.roa,
        "revenue": row.revenue,
        "net_profit": row.net_profit,
        "debt_ratio": row.debt_ratio,
        "profit_growth": row.profit_growth,
        "revenue_growth": row.revenue_growth,
        "source": row.source,
        "note": row.note,
    }


def get_latest(session: Session, symbol: str) -> dict | None:
    row = session.scalar(
        select(Fundamental)
        .where(Fundamental.symbol == symbol)
        .order_by(Fundamental.as_of_date.desc())
        .limit(1)
    )
    if row is None:
        return None
    return _row_to_dict(row)


def get_latest_bulk(session: Session, symbols: list[str]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for sym in symbols:
        rec = get_latest(session, sym)
        if rec is not None:
            out[sym] = rec
    return out


def coverage(session: Session) -> dict:
    total = session.scalar(
        select(func.count()).select_from(Instrument)
        .where(Instrument.is_active.is_(True))
    ) or 0
    with_fund = session.scalar(
        select(func.count(distinct(Fundamental.instrument_id)))
    ) or 0
    return {
        "total_active": total,
        "with_fundamental": with_fund,
        "pct_covered": round(with_fund / total * 100.0, 1) if total > 0 else 0.0,
    }