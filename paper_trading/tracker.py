"""
Update open paper signals by checking price action since signal_ts.

Walk order per signal (oldest bar first):
- If bar.low <= stop_loss  → mark stop_hit (stop wins if both touched)
- Else if bar.high >= target → mark target_hit
- Else if signal older than `expire_days` → mark expired at last close
"""

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PaperSignal, PriceBar


def update_open_signals(
    session: Session, expire_days: int = 30
) -> dict:
    """
    Walk all open signals forward through price bars, update status.
    Returns counts of transitions.
    """
    counts = {"closed_target": 0, "closed_stop": 0, "closed_expired": 0,
              "still_open": 0}

    opens = session.scalars(
        select(PaperSignal).where(PaperSignal.status == "open")
    ).all()

    now = datetime.now()
    for sig in opens:
        bars = (
            session.query(PriceBar)
            .filter(
                PriceBar.instrument_id == sig.instrument_id,
                PriceBar.ts > sig.signal_ts,
            )
            .order_by(PriceBar.ts)
            .all()
        )

        closed = False
        for bar in bars:
            if bar.low <= sig.stop_loss:
                sig.status = "stop_hit"
                sig.exit_ts = bar.ts
                sig.exit_price = sig.stop_loss
                sig.r_multiple = round(
                    (sig.exit_price - sig.entry_price)
                    / (sig.entry_price - sig.stop_loss),
                    3,
                )
                counts["closed_stop"] += 1
                closed = True
                break
            if bar.high >= sig.target:
                sig.status = "target_hit"
                sig.exit_ts = bar.ts
                sig.exit_price = sig.target
                sig.r_multiple = round(
                    (sig.exit_price - sig.entry_price)
                    / (sig.entry_price - sig.stop_loss),
                    3,
                )
                counts["closed_target"] += 1
                closed = True
                break

        if closed:
            continue

        # Expire stale signals
        if now - sig.signal_ts > timedelta(days=expire_days):
            if bars:
                last = bars[-1]
                sig.status = "expired"
                sig.exit_ts = last.ts
                sig.exit_price = float(last.close)
                sig.r_multiple = round(
                    (sig.exit_price - sig.entry_price)
                    / (sig.entry_price - sig.stop_loss),
                    3,
                )
            else:
                sig.status = "expired"
                sig.exit_ts = now
                sig.exit_price = sig.entry_price
                sig.r_multiple = 0.0
            counts["closed_expired"] += 1
        else:
            counts["still_open"] += 1

    session.commit()
    return counts


def paper_stats(session: Session) -> dict:
    """Aggregate stats over closed paper signals."""
    closed = session.scalars(
        select(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
    ).all()
    open_count = session.scalar(
        select(PaperSignal.id).where(PaperSignal.status == "open").limit(1)
    )

    if not closed:
        return {
            "total": 0, "wins": 0, "losses": 0,
            "win_rate": 0.0, "avg_r": 0.0, "profit_factor": 0.0,
            "open": 1 if open_count else 0,
        }

    wins = [s for s in closed if s.r_multiple and s.r_multiple > 0]
    losses = [s for s in closed if s.r_multiple is not None and s.r_multiple <= 0]
    avg_r = sum(s.r_multiple or 0 for s in closed) / len(closed)
    gw = sum(s.r_multiple for s in wins)
    gl = abs(sum(s.r_multiple for s in losses))
    pf = (gw / gl) if gl > 0 else 99.99

    return {
        "total": len(closed),
        "wins": len(wins), "losses": len(losses),
        "win_rate": round(len(wins) / len(closed) * 100.0, 1),
        "avg_r": round(avg_r, 3),
        "profit_factor": round(pf, 2),
        "open": sum(
            1 for _ in session.scalars(
                select(PaperSignal.id).where(PaperSignal.status == "open")
            ).all()
        ),
    }