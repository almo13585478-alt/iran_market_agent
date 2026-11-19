"""Query helpers for macro indicators."""

from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import MacroIndicator


def latest_all(session: Session) -> dict[str, dict]:
    """Return {key: {label, value, unit, as_of_date, days_ago, source}}."""
    keys = session.execute(
        select(MacroIndicator.key).distinct()
    ).all()

    out: dict[str, dict] = {}
    now = datetime.now()
    for (key,) in keys:
        row = session.scalar(
            select(MacroIndicator)
            .where(MacroIndicator.key == key)
            .order_by(MacroIndicator.as_of_date.desc())
            .limit(1)
        )
        if row is None:
            continue
        out[key] = {
            "label": row.label,
            "value": row.value,
            "unit": row.unit,
            "as_of_date": row.as_of_date,
            "days_ago": (now - row.as_of_date).days,
            "source": row.source,
        }
    return out


def history(session: Session, key: str, days: int = 90) -> list[dict]:
    cutoff = datetime.now() - timedelta(days=days)
    rows = session.scalars(
        select(MacroIndicator)
        .where(MacroIndicator.key == key)
        .where(MacroIndicator.as_of_date >= cutoff)
        .order_by(MacroIndicator.as_of_date)
    ).all()
    return [
        {
            "as_of_date": r.as_of_date,
            "value": r.value,
            "unit": r.unit,
        }
        for r in rows
    ]


def pct_change(session: Session, key: str, days: int = 30) -> float | None:
    """% change between oldest and newest in the window."""
    cutoff = datetime.now() - timedelta(days=days)
    rows = session.scalars(
        select(MacroIndicator)
        .where(MacroIndicator.key == key)
        .where(MacroIndicator.as_of_date >= cutoff)
        .order_by(MacroIndicator.as_of_date)
    ).all()
    if len(rows) < 2:
        return None
    first = rows[0].value
    last = rows[-1].value
    if first <= 0:
        return None
    return round((last - first) / first * 100.0, 2)