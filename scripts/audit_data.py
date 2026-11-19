"""
Data quality audit. Run:  python -m scripts.audit_data
Prints per-symbol: bars, valid closes, valid volumes, date range.
"""

from sqlalchemy import select, func
from database.session import get_session
from database.models import Instrument, PriceBar


def main():
    s = get_session()
    try:
        rows = s.execute(
            select(
                Instrument.symbol,
                func.count(PriceBar.id).label("n"),
                func.sum(func.iif(PriceBar.close > 0, 1, 0)).label("valid_close"),
                func.sum(func.iif(PriceBar.volume > 0, 1, 0)).label("valid_vol"),
                func.min(PriceBar.ts).label("first_ts"),
                func.max(PriceBar.ts).label("last_ts"),
            )
            .join(PriceBar, PriceBar.instrument_id == Instrument.id)
            .where(Instrument.is_active.is_(True))
            .group_by(Instrument.id)
            .order_by(func.count(PriceBar.id).desc())
        ).all()

        print(f"{'symbol':>15} | {'bars':>5} | {'close>0':>7} | {'vol>0':>6} | first -> last")
        print("-" * 80)
        for sym, n, vc, vv, fts, lts in rows[:60]:
            vc = int(vc or 0); vv = int(vv or 0)
            first = fts.strftime("%Y-%m-%d") if fts else "-"
            last = lts.strftime("%Y-%m-%d") if lts else "-"
            print(f"{sym:>15} | {n:>5} | {vc:>7} | {vv:>6} | {first} -> {last}")

        total = len(rows)
        good = sum(1 for r in rows if (r[2] or 0) >= 62 and (r[3] or 0) >= 62)
        print(f"\nTotal active with prices: {total}")
        print(f"With >=62 valid closes+volumes: {good}")
    finally:
        s.close()


if __name__ == "__main__":
    main()