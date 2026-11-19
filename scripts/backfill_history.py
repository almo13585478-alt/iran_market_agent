"""
Backfill price history for all active symbols.

API call count is unchanged: one call per symbol, just bigger payload.
Run: python -m scripts.backfill_history
     python -m scripts.backfill_history 400  (custom days)
"""

import sys
import time

from sqlalchemy import select, func

from app.logging_setup import setup_logging
from data_sources.tsetmc import TsetmcAdapter
from data_engine.sync_engine import sync_prices
from database.models import Instrument, PriceBar
from database.session import get_session


def main():
    setup_logging()
    target_days = int(sys.argv[1]) if len(sys.argv) > 1 else 400
    print(f"Target: {target_days} bars per symbol\n")

    s = get_session()
    try:
        counts = dict(
            s.execute(
                select(PriceBar.instrument_id, func.count(PriceBar.id))
                .group_by(PriceBar.instrument_id)
            ).all()
        )
        instruments = s.scalars(
            select(Instrument)
            .where(Instrument.is_active.is_(True))
            .order_by(Instrument.symbol)
        ).all()

        needs = [
            (inst, counts.get(inst.id, 0))
            for inst in instruments
            if counts.get(inst.id, 0) < target_days
        ]
        print(f"Total active: {len(instruments)}")
        print(f"Need backfill: {len(needs)}\n")

        if not needs:
            print("همه نمادها داده کافی دارند ✅")
            return

        adapter = TsetmcAdapter()
        ok = fail = 0
        for idx, (inst, have) in enumerate(needs, 1):
            try:
                r = sync_prices(adapter, s, inst.symbol, days=target_days)
                if r.ok:
                    ok += 1
                    print(f"[{idx}/{len(needs)}] {inst.symbol} ({have} → ...): {r.message}")
                else:
                    fail += 1
                    print(f"[{idx}/{len(needs)}] {inst.symbol}: FAIL {r.message}")
            except Exception as e:
                fail += 1
                print(f"[{idx}/{len(needs)}] {inst.symbol}: EXC {e}")
            time.sleep(2.0)

        print(f"\n✅ {ok} موفق، ❌ {fail} ناموفق")
    finally:
        s.close()


if __name__ == "__main__":
    main()