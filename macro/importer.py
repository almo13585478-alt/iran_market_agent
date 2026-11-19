"""
Macro indicator importer: manual add + CSV.

CSV columns:
    key,label,value,unit,as_of_date,source,notes
    (as_of_date format: YYYY-MM-DD)

Common keys:
    usd_free      — دلار آزاد (IRR)
    gold_ounce    — انس جهانی طلا (USD)
    gold_gram     — طلای ۱۸ عیار (IRR)
    coin_emami    — سکه امامی (IRR)
    inflation_yoy — تورم سالانه (%)
    interest_rate — نرخ بهره بانکی (%)
"""

import csv
from datetime import datetime
from io import StringIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.audit import log_event
from database.models import MacroIndicator


def add_indicator(
    session: Session,
    key: str,
    label: str,
    value: float,
    unit: str = "IRR",
    as_of_date: datetime | None = None,
    source: str = "manual",
    notes: str | None = None,
) -> tuple[bool, str]:
    key = (key or "").strip()
    label = (label or "").strip()
    if not key or not label:
        return False, "key و label الزامی است"

    as_of = (as_of_date or datetime.now()).replace(
        hour=0, minute=0, second=0, microsecond=0,
    )

    existing = session.scalar(
        select(MacroIndicator).where(
            MacroIndicator.key == key,
            MacroIndicator.as_of_date == as_of,
        )
    )
    if existing is None:
        existing = MacroIndicator(
            key=key, label=label, as_of_date=as_of,
        )
        session.add(existing)

    existing.value = float(value)
    existing.unit = unit
    existing.source = source
    existing.notes = notes
    session.commit()

    log_event(session, "sync", "macro_add", key,
              f"value={value} as_of={as_of.date()}",
              commit=False)
    return True, f"{label} @ {as_of.date()}: {value:,.0f} {unit}"


def import_csv(session: Session, csv_text: str) -> dict:
    results = {"added": 0, "updated": 0, "errors": []}
    try:
        reader = csv.DictReader(StringIO(csv_text))
        rows = list(reader)
    except Exception as e:
        results["errors"].append(f"CSV parse: {e}")
        return results

    for idx, row in enumerate(rows, 1):
        try:
            key = (row.get("key") or "").strip()
            label = (row.get("label") or "").strip()
            value_str = (row.get("value") or "").strip().replace(",", "")
            date_str = (row.get("as_of_date") or "").strip()

            if not key or not label or not value_str:
                results["errors"].append(f"row {idx}: missing fields")
                continue

            try:
                value = float(value_str)
            except ValueError:
                results["errors"].append(f"row {idx}: bad value ({value_str})")
                continue

            try:
                as_of = datetime.strptime(date_str, "%Y-%m-%d") if date_str \
                    else datetime.now()
            except ValueError:
                results["errors"].append(f"row {idx}: bad date ({date_str})")
                continue

            ok, msg = add_indicator(
                session, key, label, value,
                unit=row.get("unit") or "IRR",
                as_of_date=as_of,
                source=row.get("source") or "csv",
                notes=row.get("notes") or None,
            )
            if ok:
                if "updated" in msg.lower() or "بروز" in msg:
                    results["updated"] += 1
                else:
                    results["added"] += 1
            else:
                results["errors"].append(f"row {idx}: {msg}")
        except Exception as e:
            results["errors"].append(f"row {idx}: {e}")
    return results