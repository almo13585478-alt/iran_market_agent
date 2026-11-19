"""
Compare multiple parameter sets on the same universe of symbols.

Default grid (each dimension is small to keep runtime reasonable):
- thresholds: [55, 65, 75]
- atr_mult:   [1.5, 2.0, 2.5]
- rr_ratio:   [1.5, 2.0, 2.5]

Total = 27 combos. On 10 symbols with 100 bars each this runs in ~2-4 min.
"""

from dataclasses import dataclass, field
from itertools import product
from typing import Callable

from loguru import logger
from sqlalchemy.orm import Session

from backtesting.group_backtest import run_group_backtest, GroupResult


@dataclass
class ComboResult:
    threshold: float
    atr_mult: float
    rr_ratio: float
    result: GroupResult


@dataclass
class ComparisonResult:
    combos: list[ComboResult] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)


def run_grid_search(
    session: Session,
    symbols: list[str],
    thresholds: list[float] = None,
    atr_mults: list[float] = None,
    rr_ratios: list[float] = None,
    fee_pct: float = 0.005,
    min_bars: int = 80,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> ComparisonResult:
    thresholds = thresholds or [55.0, 65.0, 75.0]
    atr_mults = atr_mults or [1.5, 2.0, 2.5]
    rr_ratios = rr_ratios or [1.5, 2.0, 2.5]

    combos = list(product(thresholds, atr_mults, rr_ratios))
    out = ComparisonResult(symbols=list(symbols))

    for idx, (thr, atr, rr) in enumerate(combos, 1):
        if progress_cb:
            progress_cb(idx, len(combos), f"thr={thr}, atr={atr}, rr={rr}")
        try:
            res = run_group_backtest(
                session=session,
                symbols=symbols,
                threshold=thr,
                atr_mult=atr,
                rr_ratio=rr,
                fee_pct=fee_pct,
                min_bars=min_bars,
            )
            out.combos.append(ComboResult(
                threshold=thr, atr_mult=atr, rr_ratio=rr, result=res,
            ))
            logger.info(
                "Grid: thr={} atr={} rr={} -> {} trades, wr={}%, avgR={}",
                thr, atr, rr, res.total_trades, res.win_rate, res.avg_r,
            )
        except Exception as e:
            logger.exception("Grid combo failed: thr={} atr={} rr={}", thr, atr, rr)

    return out


def rank_combos(comparison: ComparisonResult, min_trades: int = 5) -> list[ComboResult]:
    """
    Rank by expectancy (avg R) but only if total_trades >= min_trades.
    Otherwise treat as untested and push to bottom.
    """
    def _key(c: ComboResult) -> tuple:
        enough = c.result.total_trades >= min_trades
        return (
            0 if enough else 1,
            -c.result.avg_r if enough else 999.0,
        )

    return sorted(comparison.combos, key=_key)