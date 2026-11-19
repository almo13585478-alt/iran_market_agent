"""
Fundamental import: manual add + CSV bulk.

CSV columns:
    symbol (required), as_of_date (YYYY-MM-DD, required)
    Optional: pe, pb, ps, eps, roe, roa, revenue, net_profit,
              debt_ratio, profit_growth, revenue_growth, note
"""

import csv
from datetime import datetime
from io import StringIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.audit import log_event
from database.models import Fundamental, Instrument


def _to_float(v) -> float | None:
    if v is None or v == "":
        return None
    try:
        s = str(v).strip().replace(",", "")
        return float(s)
    except (TypeError, ValueError):
        return None


def add_fundamental(
    session: Session,
    symbol: str,
    as_of_date: datetime,
    pe: float | None = None,
    pb: float | None = None,
    ps: float | None = None,
    eps: float | None = None,
    roe: float | None = None,
    roa: float | None = None,
    revenue: float | None = None,
    net_profit: float | None = None,
    debt_ratio: float | None = None,
    profit_growth: float | None = None,
    revenue_growth: float | None = None,
    source: str = "manual",
    note: str | None = None,
) -> tuple[bool, str]:
    symbol = (symbol or "").strip()
    if not symbol:
        return False, "نماد خالی"

    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return False, f"نماد {symbol} در دیتابیس نیست"

    as_of = as_of_date.replace(hour=0, minute=0, second=0, microsecond=0)
    existing = session.scalar(
        select(Fundamental).where(
            Fundamental.instrument_id == inst.id,
            Fundamental.as_of_date == as_of,
        )
    )
    if existing is None:
        existing = Fundamental(instrument_id=inst.id, symbol=symbol, as_of_date=as_of)
        session.add(existing)
    existing.pe = pe
    existing.pb = pb
    existing.ps = ps
    existing.eps = eps
    existing.roe = roe
    existing.roa = roa
    existing.revenue = revenue
    existing.net_profit = net_profit
    existing.debt_ratio = debt_ratio
    existing.profit_growth = profit_growth
    existing.revenue_growth = revenue_growth
    existing.source = source
    existing.note = note
    existing.updated_at = datetime.now()
    session.commit()

    log_event(session, "sync", "add_fundamental", symbol,
              f"as_of={as_of.date()} source={source}", commit=False)
    return True, f"{symbol} @ {as_of.date()}: ذخیره شد"


def import_csv(session: Session, csv_text: str) -> dict:
    results = {"added": 0, "skipped": 0, "errors": []}
    try:
        reader = csv.DictReader(StringIO(csv_text))
        rows = list(reader)
    except Exception as e:
        results["errors"].append(f"CSV parse error: {e}")
        return results

    for idx, row in enumerate(rows, 1):
        try:
            symbol = (row.get("symbol") or "").strip()
            date_str = (row.get("as_of_date") or "").strip()
            if not symbol or not date_str:
                results["skipped"] += 1
                continue
            try:
                as_of = datetime.strptime(date_str, "%Y-%m-%d")
            except ValueError:
                results["errors"].append(f"row {idx}: as_of_date invalid ({date_str})")
                continue

            ok, _ = add_fundamental(
                session, symbol, as_of,
                pe=_to_float(row.get("pe")),
                pb=_to_float(row.get("pb")),
                ps=_to_float(row.get("ps")),
                eps=_to_float(row.get("eps")),
                roe=_to_float(row.get("roe")),
                roa=_to_float(row.get("roa")),
                revenue=_to_float(row.get("revenue")),
                net_profit=_to_float(row.get("net_profit")),
                debt_ratio=_to_float(row.get("debt_ratio")),
                profit_growth=_to_float(row.get("profit_growth")),
                revenue_growth=_to_float(row.get("revenue_growth")),
                source="csv",
                note=row.get("note") or None,
            )
            if ok:
                results["added"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            results["errors"].append(f"row {idx}: {e}")
    return results