"""
Compute current portfolio holdings from PortfolioTransaction records.
Uses weighted-average cost method (configurable later).
"""

from dataclasses import dataclass

from sqlalchemy import select, desc
from sqlalchemy.orm import Session

from database.models import Instrument, PortfolioTransaction, PriceBar


@dataclass
class Holding:
    symbol: str
    name: str
    quantity: float
    avg_cost: float
    last_close: float | None
    market_value: float
    cost_basis: float
    unrealized_pl: float
    unrealized_pl_pct: float


def compute_holdings(session: Session) -> list[Holding]:
    txs = session.scalars(
        select(PortfolioTransaction).order_by(PortfolioTransaction.ts)
    ).all()

    by_inst: dict[int, list[PortfolioTransaction]] = {}
    for t in txs:
        by_inst.setdefault(t.instrument_id, []).append(t)

    holdings: list[Holding] = []
    for inst_id, trades in by_inst.items():
        inst = session.scalar(select(Instrument).where(Instrument.id == inst_id))
        if inst is None:
            continue

        qty = 0.0
        cost = 0.0
        for t in trades:
            if t.side == "BUY":
                cost += t.price * t.quantity + t.fee
                qty += t.quantity
            elif t.side == "SELL":
                if qty <= 0:
                    continue
                avg = cost / qty
                sell_qty = min(t.quantity, qty)
                cost -= avg * sell_qty
                qty -= sell_qty
                if qty <= 1e-9:
                    qty = 0.0
                    cost = 0.0

        if qty <= 0:
            continue

        last_bar = session.scalar(
            select(PriceBar)
            .where(PriceBar.instrument_id == inst_id)
            .order_by(desc(PriceBar.ts))
            .limit(1)
        )
        last_close = last_bar.close if last_bar else None
        market_value = (last_close * qty) if last_close else 0.0
        avg_cost = cost / qty if qty > 0 else 0.0
        unrealized_pl = market_value - cost
        unrealized_pl_pct = (unrealized_pl / cost * 100.0) if cost > 0 else 0.0

        holdings.append(
            Holding(
                symbol=inst.symbol,
                name=inst.name,
                quantity=qty,
                avg_cost=avg_cost,
                last_close=last_close,
                market_value=market_value,
                cost_basis=cost,
                unrealized_pl=unrealized_pl,
                unrealized_pl_pct=unrealized_pl_pct,
            )
        )

    holdings.sort(key=lambda h: -h.market_value)
    return holdings