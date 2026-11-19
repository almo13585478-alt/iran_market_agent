"""
Bulk re-sync all active symbols with a long history (default 200 bars).
Skips symbols that already have >= target_bars.

Run: python -m scripts.resync_all_200
     python -m scripts.resync_all_200 300   (optional days)
"""

import sys
import time

from loguru import logger
from sqlalchemy import select, func

from app.logging_setup import setup_logging
from data_sources.tsetmc import TsetmcAdapter
from data_engine.sync_engine import sync_prices
from database.models import Instrument, PriceBar
from database.session import get_session


def main():
    setup_logging()
    days = int(sys.argv[1]) if len(sys.argv) > 1 else 200

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

        to_sync = [(i, counts.get(i.id, 0)) for i in instruments]
        to_sync = [(i, n) for i, n in to_sync if n < days]
        print(f"Total active: {len(instruments)}")
        print(f"Need sync (< {days} bars): {len(to_sync)}")
        if not to_sync:
            print("همه نمادها داده کافی دارند ✅")
            return

        adapter = TsetmcAdapter()
        ok_n = fail_n = 0

        for idx, (inst, n_have) in enumerate(to_sync, 1):
            try:
                r = sync_prices(adapter, s, inst.symbol, days=days)
                if r.ok:
                    ok_n += 1
                    print(f"[{idx}/{len(to_sync)}] {inst.symbol}: {r.message}")
                else:
                    fail_n += 1
                    print(f"[{idx}/{len(to_sync)}] {inst.symbol}: FAIL - {r.message}")
            except Exception as e:
                fail_n += 1
                logger.exception("Sync failed for {}", inst.symbol)
                print(f"[{idx}/{len(to_sync)}] {inst.symbol}: EXC {e}")
            time.sleep(2.0)

        print(f"\nDone: {ok_n} ok, {fail_n} failed")
    finally:
        s.close()


if __name__ == "__main__":
    main()