"""
Order flow analysis: buy/sell pressure, dominance, smart-money trend.
"""

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from database.models import Instrument, OrderFlowDaily, PriceBar
from data_sources.tsetmc_orderflow import TsetmcOrderFlowAdapter


@dataclass
class OrderFlowSnapshot:
    ts: datetime
    # per-share totals
    buy_retail_vol: float = 0.0
    buy_inst_vol: float = 0.0
    sell_retail_vol: float = 0.0
    sell_inst_vol: float = 0.0
    # derived metrics
    buy_power: float = 0.0      # total buy / total sell
    retail_net: float = 0.0     # retail buy - retail sell (shares)
    inst_net: float = 0.0       # inst buy - inst sell
    dominant: str = "balanced"  # "retail" | "inst" | "balanced"
    smart_money_score: float = 50.0  # 0-100 (higher = more institutional buying)

    # Optional order book
    buy_limit_price: float | None = None
    buy_limit_vol: float | None = None
    sell_limit_price: float | None = None
    sell_limit_vol: float | None = None


@dataclass
class OrderFlowReport:
    symbol: str
    available: bool = False
    message: str = ""
    latest: OrderFlowSnapshot | None = None
    history: list[OrderFlowSnapshot] = field(default_factory=list)
    trend_5d: str = "unknown"   # "improving" | "deteriorating" | "stable"
    warnings: list[str] = field(default_factory=list)


def sync_order_flow(
    session: Session, adapter: TsetmcOrderFlowAdapter, symbol: str,
    days: int = 30,
) -> tuple[bool, str]:
    """Fetch and upsert order flow history for one symbol."""
    inst = session.scalar(
        select(Instrument).where(Instrument.symbol == symbol)
    )
    if inst is None:
        return False, f"نماد {symbol} یافت نشد"
    if not inst.ins_code or inst.ins_code.startswith("IRO"):
        return False, f"{symbol}: insCode معتبر نیست"

    r = adapter.fetch_client_type_history(inst.ins_code, days=days)
    if not r.ok:
        return False, f"{symbol}: {r.message}"

    # Optional: try to fetch best limits
    limits_result = adapter.fetch_best_limits(inst.ins_code)
    limits = limits_result.payload if limits_result.ok else {}

    added = updated = 0
    for row in r.payload:
        existing = session.scalar(
            select(OrderFlowDaily).where(
                OrderFlowDaily.instrument_id == inst.id,
                OrderFlowDaily.ts == row["ts"],
            )
        )
        if existing is None:
            existing = OrderFlowDaily(
                instrument_id=inst.id, symbol=symbol, ts=row["ts"],
            )
            session.add(existing)
            added += 1
        else:
            updated += 1

        existing.buy_retail_vol = row["buy_retail_vol"]
        existing.buy_inst_vol = row["buy_inst_vol"]
        existing.sell_retail_vol = row["sell_retail_vol"]
        existing.sell_inst_vol = row["sell_inst_vol"]
        existing.buy_retail_count = row["buy_retail_count"]
        existing.buy_inst_count = row["buy_inst_count"]
        existing.sell_retail_count = row["sell_retail_count"]
        existing.sell_inst_count = row["sell_inst_count"]
        existing.updated_at = datetime.now()

    # Attach limits to the most recent row only
    if limits and r.payload:
        latest_ts = r.payload[-1]["ts"]
        latest_row = session.scalar(
            select(OrderFlowDaily).where(
                OrderFlowDaily.instrument_id == inst.id,
                OrderFlowDaily.ts == latest_ts,
            )
        )
        if latest_row:
            latest_row.buy_limit_price = limits.get("buy_limit_price")
            latest_row.buy_limit_vol = limits.get("buy_limit_vol")
            latest_row.sell_limit_price = limits.get("sell_limit_price")
            latest_row.sell_limit_vol = limits.get("sell_limit_vol")

    session.commit()
    return True, f"{symbol}: {added} جدید، {updated} بروز"


def _row_to_snapshot(row: OrderFlowDaily) -> OrderFlowSnapshot:
    buy_total = row.buy_retail_vol + row.buy_inst_vol
    sell_total = row.sell_retail_vol + row.sell_inst_vol
    buy_power = (buy_total / sell_total) if sell_total > 0 else 0.0

    retail_net = row.buy_retail_vol - row.sell_retail_vol
    inst_net = row.buy_inst_vol - row.sell_inst_vol

    dominant = "balanced"
    if buy_total + sell_total > 0:
        inst_share = (row.buy_inst_vol + row.sell_inst_vol) / (buy_total + sell_total)
        if inst_share > 0.6:
            dominant = "inst"
        elif inst_share < 0.25:
            dominant = "retail"

    # Smart money score: 0 (institutional selling heavily) to 100 (buying heavily)
    # Normalize inst_net by total volume
    total_vol = buy_total + sell_total
    if total_vol > 0:
        net_ratio = inst_net / total_vol  # -1 .. +1
        smart_money_score = 50.0 + net_ratio * 50.0
        smart_money_score = max(0.0, min(100.0, smart_money_score))
    else:
        smart_money_score = 50.0

    return OrderFlowSnapshot(
        ts=row.ts,
        buy_retail_vol=row.buy_retail_vol,
        buy_inst_vol=row.buy_inst_vol,
        sell_retail_vol=row.sell_retail_vol,
        sell_inst_vol=row.sell_inst_vol,
        buy_power=round(buy_power, 2),
        retail_net=retail_net,
        inst_net=inst_net,
        dominant=dominant,
        smart_money_score=round(smart_money_score, 1),
        buy_limit_price=row.buy_limit_price,
        buy_limit_vol=row.buy_limit_vol,
        sell_limit_price=row.sell_limit_price,
        sell_limit_vol=row.sell_limit_vol,
    )


def analyze(session: Session, symbol: str, days: int = 30) -> OrderFlowReport:
    """Load order flow for a symbol from DB (no network)."""
    rpt = OrderFlowReport(symbol=symbol)

    inst = session.scalar(
        select(Instrument).where(Instrument.symbol == symbol)
    )
    if inst is None:
        rpt.message = f"نماد {symbol} یافت نشد"
        return rpt

    rows = session.scalars(
        select(OrderFlowDaily)
        .where(OrderFlowDaily.instrument_id == inst.id)
        .order_by(OrderFlowDaily.ts)
        .limit(days)
    ).all()

    if not rows:
        rpt.message = (
            f"هیچ داده order-flow برای {symbol} ذخیره نشده. "
            "از دکمه «دریافت order-flow» بالای صفحه استفاده کنید."
        )
        rpt.warnings.append(
            "TSETMC این endpoint را گاهی قطع می‌کند. اگر خطا داد، "
            "بعداً دوباره امتحان کنید."
        )
        return rpt

    rpt.available = True
    rpt.history = [_row_to_snapshot(r) for r in rows]
    rpt.latest = rpt.history[-1]
    rpt.message = f"{len(rows)} روز داده"

    # 5-day trend
    if len(rpt.history) >= 5:
        last5 = rpt.history[-5:]
        avg_sm_early = sum(s.smart_money_score for s in last5[:2]) / 2
        avg_sm_late = sum(s.smart_money_score for s in last5[-2:]) / 2
        delta = avg_sm_late - avg_sm_early
        if delta > 5:
            rpt.trend_5d = "improving"
        elif delta < -5:
            rpt.trend_5d = "deteriorating"
        else:
            rpt.trend_5d = "stable"

    return rpt


def bulk_sync(
    session: Session, symbols: list[str] | None = None, days: int = 30,
    progress_cb=None,
) -> dict:
    """Sync order flow for many symbols."""
    from database.models import Instrument as I

    q = select(I).where(I.is_active.is_(True)).order_by(I.symbol)
    if symbols:
        q = q.where(I.symbol.in_(symbols))
    instruments = session.scalars(q).all()

    # Only sync symbols we have prices for
    symbols_with_prices = {
        r[0] for r in session.execute(
            select(PriceBar.instrument_id).distinct()
        ).all()
    }
    instruments = [i for i in instruments if i.id in symbols_with_prices]

    adapter = TsetmcOrderFlowAdapter()
    ok = fail = 0
    total = len(instruments)
    errors = []

    for idx, inst in enumerate(instruments, 1):
        try:
            success, msg = sync_order_flow(
                session, adapter, inst.symbol, days=days,
            )
            if success:
                ok += 1
            else:
                fail += 1
                if len(errors) < 20:
                    errors.append(msg)
        except Exception as e:
            fail += 1
            errors.append(f"{inst.symbol}: {e}")

        if progress_cb:
            progress_cb(idx, total, inst.symbol)

        import time
        time.sleep(1.5)

    return {
        "ok": ok, "fail": fail, "total": total, "errors": errors,
    }