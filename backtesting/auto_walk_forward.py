"""
Automated walk-forward: run combos, persist results, pick active.

Rules:
- Only "robust" verdicts compete for is_active
- A new config must have test_avg_r > current_active.test_avg_r to swap
- If no robust, keep current active unchanged
- All combos stored (with is_active=False except the winner)
"""

from dataclasses import dataclass, field
from datetime import datetime
from itertools import product
from typing import Callable

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from backtesting.walk_forward import walk_forward_combo
from backtesting.group_backtest import pick_top_eligible_symbols
from database.audit import log_event
from database.models import StrategyConfig


@dataclass
class AutoWFResult:
    ok: bool = True
    message: str = ""
    n_combos: int = 0
    n_robust: int = 0
    n_fragile: int = 0
    n_overfit: int = 0
    best_test_avg_r: float | None = None
    best_combo: dict | None = None
    active_config_id: int | None = None
    swapped: bool = False
    warning: str | None = None
    computed_at: datetime = field(default_factory=datetime.now)


def _current_active(session: Session, strategy: str) -> StrategyConfig | None:
    return session.scalar(
        select(StrategyConfig)
        .where(StrategyConfig.strategy == strategy)
        .where(StrategyConfig.is_active.is_(True))
        .order_by(StrategyConfig.applied_at.desc())
        .limit(1)
    )


def run_auto_walk_forward(
    session: Session,
    strategy: str = "v2b",
    thresholds: list[float] | None = None,
    atr_mults: list[float] | None = None,
    rr_ratios: list[float] | None = None,
    n_symbols: int = 20,
    min_bars: int = 120,
    progress_cb: Callable[[int, int, str], None] | None = None,
) -> AutoWFResult:
    """
    Run the grid, persist results, pick the new active config.
    """
    thresholds = thresholds or [55.0, 65.0, 75.0]
    atr_mults = atr_mults or [1.5, 2.0, 2.5]
    rr_ratios = rr_ratios or [1.5, 2.0, 2.5]

    result = AutoWFResult()
    symbols = pick_top_eligible_symbols(session, min_bars=min_bars, limit=n_symbols)

    if not symbols:
        result.ok = False
        result.message = (
            f"نمادی با ≥{min_bars} کندل موجود نیست. "
            f"ابتدا سینک کنید یا backfill_history را اجرا کنید."
        )
        return result

    combos = list(product(thresholds, atr_mults, rr_ratios))
    result.n_combos = len(combos)

    logger.info(
        "Auto-WF: strategy={} combos={} symbols={}",
        strategy, len(combos), len(symbols),
    )

    all_results = []
    for idx, (thr, atr, rr) in enumerate(combos, 1):
        label = f"thr={thr} atr={atr} rr={rr}"
        if progress_cb:
            progress_cb(idx, len(combos), label)

        try:
            wf = walk_forward_combo(
                session=session,
                symbols=symbols,
                threshold=thr,
                atr_mult=atr,
                rr_ratio=rr,
                min_bars=min_bars,
            )
        except Exception as e:
            logger.exception("Auto-WF combo failed: {}", label)
            continue

        cfg = StrategyConfig(
            strategy=strategy,
            threshold=thr,
            atr_mult=atr,
            rr_ratio=rr,
            train_trades=wf.train_trades,
            train_avg_r=round(wf.train_avg_r, 3),
            test_trades=wf.test_trades,
            test_win_rate=round(wf.test_win_rate, 1),
            test_avg_r=round(wf.test_avg_r, 3),
            test_profit_factor=round(wf.test_profit_factor, 2),
            degradation=round(wf.degradation, 2),
            verdict=wf.verdict,
            is_active=False,
            source="auto",
            computed_at=datetime.now(),
        )
        session.add(cfg)
        all_results.append((cfg, wf))

        if wf.verdict == "robust":
            result.n_robust += 1
        elif wf.verdict == "fragile":
            result.n_fragile += 1
        elif wf.verdict == "overfit":
            result.n_overfit += 1

    session.commit()

    # Pick best robust
    robust_configs = [(c, w) for c, w in all_results if w.verdict == "robust"]
    if not robust_configs:
        result.ok = True
        result.message = (
            f"{len(all_results)} ترکیب تست شد، هیچ robust نیست. "
            f"پیکربندی فعال بدون تغییر ماند."
        )
        log_event(
            session, "system", "auto_wf",
            subject=strategy,
            details=f"no_robust results={len(all_results)}",
        )
        return result

    robust_configs.sort(key=lambda cw: -cw[1].test_avg_r)
    best_cfg, best_wf = robust_configs[0]
    result.best_test_avg_r = round(best_wf.test_avg_r, 3)
    result.best_combo = {
        "threshold": best_cfg.threshold,
        "atr_mult": best_cfg.atr_mult,
        "rr_ratio": best_cfg.rr_ratio,
        "test_avg_r": round(best_wf.test_avg_r, 3),
        "test_win_rate": round(best_wf.test_win_rate, 1),
        "test_trades": best_wf.test_trades,
    }

    current = _current_active(session, strategy)
    swap_decision = False

    if current is None:
        swap_decision = True
        reason = "no previous active"
    elif best_cfg.test_avg_r > current.test_avg_r:
        swap_decision = True
        reason = (
            f"new {best_cfg.test_avg_r:.3f} > "
            f"current {current.test_avg_r:.3f}"
        )
    else:
        swap_decision = False
        reason = (
            f"new {best_cfg.test_avg_r:.3f} <= "
            f"current {current.test_avg_r:.3f} — keeping current"
        )

    if swap_decision:
        if current is not None:
            current.is_active = False
        best_cfg.is_active = True
        best_cfg.applied_at = datetime.now()
        best_cfg.notes = (
            f"Auto-accepted on {datetime.now().strftime('%Y-%m-%d %H:%M')}. "
            f"{reason}"
        )
        result.active_config_id = best_cfg.id
        result.swapped = True
        result.message = (
            f"{len(all_results)} ترکیب، {result.n_robust} robust. "
            f"پیکربندی فعال به thr={best_cfg.threshold}, "
            f"atr={best_cfg.atr_mult}, rr={best_cfg.rr_ratio} "
            f"با test_avgR={best_cfg.test_avg_r:.3f} تغییر یافت."
        )
    else:
        result.swapped = False
        result.warning = (
            f"ترکیب جدید با test_avgR={best_cfg.test_avg_r:.3f} "
            f"از فعال فعلی ({current.test_avg_r:.3f}) بهتر نبود. "
            f"فعال بدون تغییر ماند."
        )
        result.message = (
            f"{len(all_results)} ترکیب، {result.n_robust} robust — "
            f"بدون تعویض."
        )

    session.commit()

    log_event(
        session, "system", "auto_wf",
        subject=strategy,
        details=(
            f"combos={len(all_results)} robust={result.n_robust} "
            f"fragile={result.n_fragile} overfit={result.n_overfit} "
            f"swapped={result.swapped} reason={reason}"
        ),
    )

    return result


def get_active(session: Session, strategy: str = "v2b") -> StrategyConfig | None:
    return _current_active(session, strategy)


def get_history(
    session: Session, strategy: str = "v2b", limit: int = 100,
) -> list[StrategyConfig]:
    return session.scalars(
        select(StrategyConfig)
        .where(StrategyConfig.strategy == strategy)
        .order_by(StrategyConfig.computed_at.desc())
        .limit(limit)
    ).all()


def accept_manually(
    session: Session, config_id: int,
) -> tuple[bool, str]:
    """
    Manually activate a config. Deactivates any other active for same strategy.
    """
    cfg = session.scalar(
        select(StrategyConfig).where(StrategyConfig.id == config_id)
    )
    if cfg is None:
        return False, "پیکربندی یافت نشد"

    for old in session.scalars(
        select(StrategyConfig)
        .where(StrategyConfig.strategy == cfg.strategy)
        .where(StrategyConfig.is_active.is_(True))
    ).all():
        old.is_active = False

    cfg.is_active = True
    cfg.applied_at = datetime.now()
    cfg.notes = (
        (cfg.notes or "") +
        f" | Manually applied on {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    session.commit()

    log_event(
        session, "system", "auto_wf_manual_accept",
        subject=cfg.strategy,
        details=f"config_id={config_id}",
    )
    return True, (
        f"پیکربندی thr={cfg.threshold}, atr={cfg.atr_mult}, "
        f"rr={cfg.rr_ratio} فعال شد."
    )