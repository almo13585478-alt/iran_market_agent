"""
Performance reports over closed paper signals.
Groups by strategy, symbol, and time window.

Also respects market state at signal time if `market_state_at_signal`
is available later — currently we only have signal_ts.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PaperSignal


@dataclass
class PerfBucket:
    label: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    profit_factor: float = 0.0
    total_r: float = 0.0
    max_dd_r: float = 0.0


@dataclass
class PerformanceReport:
    overall: PerfBucket = field(default_factory=lambda: PerfBucket(label="کل"))
    by_strategy: dict[str, PerfBucket] = field(default_factory=dict)
    by_symbol: dict[str, PerfBucket] = field(default_factory=dict)
    by_month: dict[str, PerfBucket] = field(default_factory=dict)
    open_count: int = 0


def _summarize(label: str, signals: list[PaperSignal]) -> PerfBucket:
    closed = [s for s in signals if s.r_multiple is not None]
    b = PerfBucket(label=label, total=len(closed))
    if not closed:
        return b

    wins = [s for s in closed if (s.r_multiple or 0) > 0]
    losses = [s for s in closed if (s.r_multiple or 0) <= 0]
    b.wins = len(wins)
    b.losses = len(losses)
    b.win_rate = round(len(wins) / len(closed) * 100.0, 1)
    b.total_r = round(sum(s.r_multiple or 0 for s in closed), 2)
    b.avg_r = round(b.total_r / len(closed), 3)

    gw = sum(s.r_multiple for s in wins)
    gl = abs(sum(s.r_multiple for s in losses))
    b.profit_factor = round(gw / gl, 2) if gl > 0 else 99.99

    # Equity curve max drawdown
    sorted_sigs = sorted(closed, key=lambda s: s.exit_ts or s.signal_ts)
    eq = [0.0]
    for s in sorted_sigs:
        eq.append(eq[-1] + (s.r_multiple or 0))
    peak, mdd = eq[0], 0.0
    for v in eq:
        if v > peak:
            peak = v
        if peak - v > mdd:
            mdd = peak - v
    b.max_dd_r = round(mdd, 2)

    return b


def build_report(session: Session) -> PerformanceReport:
    sigs = session.scalars(select(PaperSignal)).all()
    closed = [s for s in sigs if s.status in ("target_hit", "stop_hit", "expired")]
    opens = [s for s in sigs if s.status == "open"]

    rpt = PerformanceReport()
    rpt.open_count = len(opens)
    rpt.overall = _summarize("کل", closed)

    by_strat: dict[str, list[PaperSignal]] = {}
    by_sym: dict[str, list[PaperSignal]] = {}
    by_month: dict[str, list[PaperSignal]] = {}
    for s in closed:
        by_strat.setdefault(s.strategy, []).append(s)
        by_sym.setdefault(s.symbol, []).append(s)
        if s.exit_ts or s.signal_ts:
            month = (s.exit_ts or s.signal_ts).strftime("%Y-%m")
            by_month.setdefault(month, []).append(s)

    rpt.by_strategy = {k: _summarize(k, v) for k, v in by_strat.items()}
    rpt.by_symbol = {k: _summarize(k, v) for k, v in by_sym.items()}
    rpt.by_month = {k: _summarize(k, v) for k, v in by_month.items()}
    return rpt