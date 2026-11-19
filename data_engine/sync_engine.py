"""
Sync engine with circuit breaker + lazy insCode resolution + audit log.
"""

import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from data_sources.base_adapter import BaseAdapter
from database.models import Instrument, PriceBar, DataSourceStatus
from database.audit import log_event


@dataclass
class SyncResult:
    ok: bool
    message: str
    added: int = 0
    updated: int = 0
    failed: int = 0
    skipped: int = 0


def _upsert_status(session, name, ok, error=None):
    st = session.scalar(
        select(DataSourceStatus).where(DataSourceStatus.source_name == name)
    )
    if st is None:
        st = DataSourceStatus(source_name=name)
        session.add(st)
    st.is_healthy = ok
    if ok:
        st.last_success_at = datetime.now()
        st.last_error = None
    else:
        st.last_error = (error or "")[:2000]
    session.commit()


def sync_instruments(adapter: BaseAdapter, session: Session) -> SyncResult:
    result = adapter.fetch_instruments(stocks_only=True)
    tag = f"{adapter.name}_instruments"
    if not result.ok:
        _upsert_status(session, tag, False, result.message)
        log_event(session, "sync", "sync_instruments", adapter.name,
                  result.message, success=False)
        return SyncResult(ok=False, message=result.message)

    added = updated = 0
    for row in result.payload:
        sym = row["symbol"]
        isin = row.get("isin") or None
        ins_code_raw = row.get("ins_code") or None
        ins_code = None
        if ins_code_raw:
            s = str(ins_code_raw).strip()
            if s and not s.startswith("IRO"):
                ins_code = s

        existing = None
        if isin:
            existing = session.scalar(
                select(Instrument).where(Instrument.isin == isin)
            )
        if existing is None and ins_code:
            existing = session.scalar(
                select(Instrument).where(Instrument.ins_code == ins_code)
            )
        if existing is None:
            existing = session.scalar(
                select(Instrument).where(Instrument.symbol == sym)
            )

        if existing is not None:
            existing.symbol = sym
            existing.name = row.get("name") or existing.name
            if isin:
                existing.isin = isin
            if ins_code:
                existing.ins_code = ins_code
            existing.flow = row.get("flow") or existing.flow
            existing.market = row.get("market") or existing.market
            existing.is_active = row.get("is_active", True)
            updated += 1
        else:
            session.add(
                Instrument(
                    symbol=sym, name=row.get("name") or sym,
                    isin=isin, ins_code=ins_code, flow=row.get("flow"),
                    market=row.get("market") or "TSE",
                    industry=row.get("industry"),
                    is_active=row.get("is_active", True),
                )
            )
            added += 1

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        _upsert_status(session, tag, False, str(e))
        log_event(session, "sync", "sync_instruments", adapter.name,
                  str(e), success=False)
        return SyncResult(ok=False, message=f"خطای ذخیره: {e}")

    _upsert_status(session, tag, True)
    msg = f"{added} نماد جدید، {updated} بروزرسانی"
    log_event(session, "sync", "sync_instruments", adapter.name, msg)
    logger.info("Instruments sync: {}", msg)
    return SyncResult(ok=True, message=msg, added=added, updated=updated)


def sync_prices(
    adapter: BaseAdapter, session: Session, symbol: str, days: int = 60
) -> SyncResult:
    inst = session.scalar(select(Instrument).where(Instrument.symbol == symbol))
    if inst is None:
        return SyncResult(ok=False, message=f"نماد {symbol} در دیتابیس نیست")
    if not inst.is_active:
        return SyncResult(ok=False, message=f"{symbol} غیرفعال است (مشتقه/آپشن)")

    tag = f"{adapter.name}_prices"

    if hasattr(adapter, "resolve_ins_code_by_symbol"):
        if not inst.ins_code or inst.ins_code.startswith("IRO"):
            code = adapter.resolve_ins_code_by_symbol(symbol)
            if not code:
                _upsert_status(session, tag, False, f"insCode برای {symbol} پیدا نشد")
                return SyncResult(ok=False, message=f"{symbol}: insCode پیدا نشد")
            inst.ins_code = code
            try:
                session.commit()
            except Exception as e:
                session.rollback()
                return SyncResult(ok=False, message=f"خطای ذخیره insCode: {e}")

    if inst.ins_code and hasattr(adapter, "fetch_prices_by_ins_code"):
        result = adapter.fetch_prices_by_ins_code(inst.ins_code, days)
    else:
        result = adapter.fetch_prices(symbol, days)

    if not result.ok:
        _upsert_status(session, tag, False, result.message)
        return SyncResult(ok=False, message=f"{symbol}: {result.message}")

    if not result.payload:
        msg = f"{symbol}: payload خالی"
        _upsert_status(session, tag, False, msg)
        return SyncResult(ok=False, message=msg)

    added = updated = 0
    for bar in result.payload:
        existing = session.scalar(
            select(PriceBar).where(
                PriceBar.instrument_id == inst.id,
                PriceBar.ts == bar["ts"],
            )
        )
        if existing is not None:
            existing.open = bar["open"]
            existing.high = bar["high"]
            existing.low = bar["low"]
            existing.close = bar["close"]
            existing.volume = bar["volume"]
            existing.value = bar["value"]
            existing.source = adapter.name
            updated += 1
        else:
            session.add(
                PriceBar(
                    instrument_id=inst.id, ts=bar["ts"],
                    open=bar["open"], high=bar["high"],
                    low=bar["low"], close=bar["close"],
                    volume=bar["volume"], value=bar["value"],
                    source=adapter.name,
                )
            )
            added += 1

    if added + updated == 0:
        return SyncResult(ok=False, message=f"{symbol}: هیچ رکورد جدید/بروز نشد")

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        _upsert_status(session, tag, False, str(e))
        return SyncResult(ok=False, message=f"خطای ذخیره: {e}")

    _upsert_status(session, tag, True)
    return SyncResult(
        ok=True, message=f"{symbol}: {added} جدید، {updated} بروز",
        added=added, updated=updated,
    )


def sync_all_prices(
    adapter: BaseAdapter,
    session: Session,
    days: int = 60,
    max_instruments: int = 50,
    progress_cb: Callable[[int, int, str], None] | None = None,
    pause_seconds: float = 2.5,
    max_consecutive_failures: int = 10,
) -> SyncResult:
    q = (
        select(Instrument)
        .where(Instrument.is_active.is_(True))
        .order_by(Instrument.symbol)
    )
    instruments = session.scalars(q).all()
    to_sync = instruments[:max_instruments]
    total = len(to_sync)

    if total == 0:
        return SyncResult(ok=True, message="هیچ نماد فعالی برای همگام‌سازی نیست")

    ok_count = fail_count = 0
    consecutive_failures = 0
    aborted_reason = None

    for i, inst in enumerate(to_sync, start=1):
        r = sync_prices(adapter, session, inst.symbol, days)
        if r.ok:
            ok_count += 1
            consecutive_failures = 0
        else:
            fail_count += 1
            consecutive_failures += 1
            if consecutive_failures >= max_consecutive_failures:
                aborted_reason = (
                    f"پس از {consecutive_failures} خطای پیاپی متوقف شد "
                    "(احتمالاً rate-limit TSETMC)."
                )
                break
        if progress_cb:
            progress_cb(i, total, inst.symbol)
        if pause_seconds > 0:
            time.sleep(pause_seconds)

    msg = f"{ok_count} موفق، {fail_count} ناموفق از {total} نماد"
    if aborted_reason:
        msg = f"{msg} — {aborted_reason}"
        log_event(session, "sync", "sync_prices_bulk", adapter.name,
                  msg, success=False)
        return SyncResult(
            ok=False, message=msg, added=ok_count, failed=fail_count,
        )

    log_event(session, "sync", "sync_prices_bulk", adapter.name, msg)
    return SyncResult(
        ok=True, message=msg, added=ok_count, failed=fail_count,
    )