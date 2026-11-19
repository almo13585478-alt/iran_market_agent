"""
Industry aggregation. Groups ScoreBreakdowns by industry (or market fallback).

The caller provides `industry_map` because industry data may be manually
loaded or fall back to market type. This module never queries TSETMC.
"""

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Instrument


@dataclass
class IndustryStat:
    industry: str
    n_symbols: int = 0
    avg_score: float = 0.0
    avg_rsi: float = 0.0
    pct_uptrend: float = 0.0
    top_symbols: list[tuple[str, float]] = field(default_factory=list)
    weak_symbols: list[tuple[str, float]] = field(default_factory=list)
    score_70plus: int = 0


def build_industry_map(session: Session) -> dict[str, str]:
    """Return {symbol: industry_label}. Falls back to [market] if industry empty."""
    insts = session.scalars(select(Instrument)).all()
    out: dict[str, str] = {}
    for i in insts:
        label = i.industry.strip() if i.industry else f"[{i.market}]"
        if not label:
            label = "[نامشخص]"
        out[i.symbol] = label
    return out


def aggregate_by_industry(scores, industry_map: dict[str, str]) -> list[IndustryStat]:
    """Group ScoreBreakdowns by industry and compute per-industry stats."""
    if not scores:
        return []

    groups: dict[str, list] = {}
    for s in scores:
        ind = industry_map.get(s.symbol, "[نامشخص]")
        groups.setdefault(ind, []).append(s)

    stats: list[IndustryStat] = []
    for ind, items in groups.items():
        if not items:
            continue
        n = len(items)
        avg_score = sum(x.total for x in items) / n

        rsis = [
            x.technical.get("rsi14", 50.0)
            for x in items
            if x.technical and x.technical.get("ok")
        ]
        avg_rsi = sum(rsis) / len(rsis) if rsis else 0.0

        uptrends = sum(
            1 for x in items
            if x.technical and x.technical.get("trend") == "up"
        )
        pct_uptrend = uptrends / n * 100.0

        sorted_items = sorted(items, key=lambda x: -x.total)
        top = [(x.symbol, round(x.total, 1)) for x in sorted_items[:3]]
        weak = [(x.symbol, round(x.total, 1)) for x in sorted_items[-3:][::-1]]

        score_70plus = sum(1 for x in items if x.total >= 70.0)

        stats.append(IndustryStat(
            industry=ind,
            n_symbols=n,
            avg_score=round(avg_score, 1),
            avg_rsi=round(avg_rsi, 1),
            pct_uptrend=round(pct_uptrend, 1),
            top_symbols=top,
            weak_symbols=weak,
            score_70plus=score_70plus,
        ))

    stats.sort(key=lambda x: -x.avg_score)
    return stats