"""
Advanced paper-trading analytics.

Complements the basic paper_stats with:
- Per-strategy deep breakdown (streaks, expectancy, r-distribution)
- Weekly/monthly buckets
- Equity drawdown series
- Signal quality vs outcome
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import PaperSignal


@dataclass
class StrategyAnalytics:
    strategy: str
    total: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_r: float = 0.0
    total_r: float = 0.0
    profit_factor: float = 0.0
    expectancy: float = 0.0
    max_win_streak: int = 0
    max_loss_streak: int = 0
    current_streak: int = 0
    r_bins: dict[str, int] = field(default_factory=dict)


@dataclass
class WeeklyBucket:
    week_start: datetime
    total: int = 0
    wins: int = 0
    avg_r: float = 0.0
    total_r: float = 0.0


@dataclass
class DrawdownPoint:
    ts: datetime
    equity_r: float
    drawdown_r: float


def _sorted_closed(session: Session, strategy: str | None = None) -> list[PaperSignal]:
    q = (
        select(PaperSignal)
        .where(PaperSignal.status.in_(["target_hit", "stop_hit", "expired"]))
        .where(PaperSignal.exit_ts.isnot(None))
        .order_by(PaperSignal.exit_ts)
    )
    if strategy:
        q = q.where(PaperSignal.strategy == strategy)
    return session.scalars(q).all()


def compute_strategy_analytics(
    session: Session, strategy: str
) -> StrategyAnalytics:
    sigs = _sorted_closed(session, strategy)
    out = StrategyAnalytics(strategy=strategy)
    if not sigs:
        return out

    out.total = len(sigs)
    r_values = [s.r_multiple or 0.0 for s in sigs]
    out.wins = sum(1 for r in r_values if r > 0)
    out.losses = out.total - out.wins
    out.win_rate = round(out.wins / out.total * 100.0, 1)
    out.total_r = round(sum(r_values), 2)
    out.avg_r = round(out.total_r / out.total, 3)
    out.expectancy = out.avg_r

    gross_win = sum(r for r in r_values if r > 0)
    gross_loss = abs(sum(r for r in r_values if r <= 0))
    out.profit_factor = round(gross_win / gross_loss, 2) if gross_loss > 0 else 99.99

    # streaks
    max_win = max_loss = 0
    cur = 0
    for r in r_values:
        if r > 0:
            cur = cur + 1 if cur >= 0 else 1
            max_win = max(max_win, cur)
        else:
            cur = cur - 1 if cur <= 0 else -1
            max_loss = max(max_loss, abs(cur))
    out.max_win_streak = max_win
    out.max_loss_streak = max_loss
    out.current_streak = cur

    # r bins
    bins = {"< -2": 0, "-2 to -1": 0, "-1 to 0": 0,
            "0 to +1": 0, "+1 to +2": 0, "> +2": 0}
    for r in r_values:
        if r < -2:
            bins["< -2"] += 1
        elif r < -1:
            bins["-2 to -1"] += 1
        elif r <= 0:
            bins["-1 to 0"] += 1
        elif r <= 1:
            bins["0 to +1"] += 1
        elif r <= 2:
            bins["+1 to +2"] += 1
        else:
            bins["> +2"] += 1
    out.r_bins = bins

    return out


def compute_weekly_buckets(
    session: Session, strategy: str | None = None, weeks: int = 12
) -> list[WeeklyBucket]:
    cutoff = datetime.now() - timedelta(weeks=weeks)
    sigs = [
        s for s in _sorted_closed(session, strategy)
        if s.exit_ts and s.exit_ts >= cutoff
    ]

    buckets: dict[datetime, list[PaperSignal]] = {}
    for s in sigs:
        ws = s.exit_ts - timedelta(days=s.exit_ts.weekday())
        ws = ws.replace(hour=0, minute=0, second=0, microsecond=0)
        buckets.setdefault(ws, []).append(s)

    out = []
    for ws in sorted(buckets.keys()):
        items = buckets[ws]
        r_values = [x.r_multiple or 0.0 for x in items]
        out.append(WeeklyBucket(
            week_start=ws,
            total=len(items),
            wins=sum(1 for r in r_values if r > 0),
            avg_r=round(sum(r_values) / len(r_values), 3),
            total_r=round(sum(r_values), 2),
        ))
    return out


def compute_drawdown_series(
    session: Session, strategy: str | None = None
) -> list[DrawdownPoint]:
    sigs = _sorted_closed(session, strategy)
    out = []
    equity = 0.0
    peak = 0.0
    for s in sigs:
        equity += s.r_multiple or 0.0
        if equity > peak:
            peak = equity
        dd = peak - equity
        out.append(DrawdownPoint(
            ts=s.exit_ts,
            equity_r=round(equity, 3),
            drawdown_r=round(dd, 3),
        ))
    return out


def signal_quality_vs_outcome(session: Session) -> dict:
    """Correlate signal_ts score (score_at_signal) with realized r_multiple."""
    sigs = _sorted_closed(session)
    if len(sigs) < 5:
        return {"n": len(sigs), "correlation": None, "message": "نمونه کافی نیست"}

    scores = [s.score_at_signal or 0.0 for s in sigs]
    rs = [s.r_multiple or 0.0 for s in sigs]
    n = len(scores)
    mx = sum(scores) / n
    my = sum(rs) / n
    num = sum((scores[i] - mx) * (rs[i] - my) for i in range(n))
    dx = sum((scores[i] - mx) ** 2 for i in range(n)) ** 0.5
    dy = sum((rs[i] - my) ** 2 for i in range(n)) ** 0.5
    if dx == 0 or dy == 0:
        return {"n": n, "correlation": 0.0, "message": "واریانس صفر"}
    corr = round(num / (dx * dy), 3)
    interp = (
        "قوی مثبت — امتیاز بالاتر → R بالاتر" if corr > 0.3 else
        "متوسط مثبت" if corr > 0.1 else
        "قوی منفی — امتیاز بالا به معنی R پایین!" if corr < -0.3 else
        "بی‌معنا — امتیاز قدرت پیش‌بینی ندارد"
    )
    return {"n": n, "correlation": corr, "message": interp}


def all_strategies(session: Session) -> list[str]:
    rows = session.execute(
        select(PaperSignal.strategy).distinct()
    ).all()
    return sorted({r[0] for r in rows if r[0]})