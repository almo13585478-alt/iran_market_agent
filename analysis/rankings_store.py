"""
Persist and load ScoreBreakdown rankings across restarts.

Key insight: `technical` dict is the only non-trivial field. Everything else
is primitives. So we serialize technical as JSON and reconstruct ScoreBreakdown.
"""

import json
from datetime import datetime, timedelta

from sqlalchemy import select, func, delete
from sqlalchemy.orm import Session

from database.models import RankingSnapshot


def _sb_to_row(sb, batch_ts: datetime, rank: int, total: int, min_bars: int):
    """Convert ScoreBreakdown → RankingSnapshot row."""
    def _safe_json(obj):
        try:
            return json.dumps(obj, ensure_ascii=False, default=str)
        except Exception:
            return "{}"

    return RankingSnapshot(
        batch_ts=batch_ts,
        symbol=sb.symbol,
        total=float(sb.total),
        technical_score=float(sb.technical_score),
        liquidity_score=float(sb.liquidity_score),
        momentum_score=float(sb.momentum_score),
        risk_score=float(sb.risk_score),
        trend_strength_score=float(sb.trend_strength_score),
        fundamental_score=float(sb.fundamental_score),
        technical_json=_safe_json(sb.technical or {}),
        reasons_json=_safe_json(sb.reasons or []),
        red_flags_json=_safe_json(sb.red_flags or []),
        rank_position=rank,
        total_ranked=total,
        min_bars_used=min_bars,
    )


def _row_to_sb(row):
    """Convert RankingSnapshot → ScoreBreakdown."""
    from decision_engine.scoring import DEFAULT_WEIGHTS, ScoreBreakdown

    def _safe_load(s, fallback):
        try:
            return json.loads(s) if s else fallback
        except Exception:
            return fallback

    return ScoreBreakdown(
        symbol=row.symbol,
        total=row.total,
        technical_score=row.technical_score,
        liquidity_score=row.liquidity_score,
        momentum_score=row.momentum_score,
        risk_score=row.risk_score,
        trend_strength_score=row.trend_strength_score,
        fundamental_score=row.fundamental_score,
        weights=dict(DEFAULT_WEIGHTS),
        reasons=_safe_load(row.reasons_json, []),
        red_flags=_safe_load(row.red_flags_json, []),
        technical=_safe_load(row.technical_json, {}),
        computed_at=row.batch_ts,
    )


def save_rankings(
    session: Session,
    rankings: list,
    min_bars: int = 60,
    keep_last_n: int = 5,
) -> dict:
    """
    Save a fresh batch. Old batches are pruned (keep_last_n). Idempotent
    within a session (deletes current batch if same second — rare).
    """
    if not rankings:
        return {"ok": False, "message": "لیست خالی است", "n": 0}

    batch_ts = datetime.now()
    total = len(rankings)
    sorted_rank = sorted(rankings, key=lambda x: -x.total)

    added = 0
    for i, sb in enumerate(sorted_rank, 1):
        session.add(_sb_to_row(sb, batch_ts, i, total, min_bars))
        added += 1

    try:
        session.commit()
    except Exception as e:
        session.rollback()
        return {"ok": False, "message": f"خطای ذخیره: {e}", "n": 0}

    # Prune old batches
    try:
        batches = session.execute(
            select(RankingSnapshot.batch_ts)
            .distinct()
            .order_by(RankingSnapshot.batch_ts.desc())
        ).all()
        if len(batches) > keep_last_n:
            to_delete = [b[0] for b in batches[keep_last_n:]]
            session.execute(
                delete(RankingSnapshot).where(
                    RankingSnapshot.batch_ts.in_(to_delete)
                )
            )
            session.commit()
    except Exception:
        pass

    return {
        "ok": True,
        "message": f"{added} نماد ذخیره شد",
        "n": added,
        "batch_ts": batch_ts,
    }


def load_latest_rankings(session: Session) -> list | None:
    """Load the most recent batch. Returns None if empty."""
    latest_ts = session.scalar(
        select(RankingSnapshot.batch_ts)
        .order_by(RankingSnapshot.batch_ts.desc())
        .limit(1)
    )
    if latest_ts is None:
        return None

    rows = session.scalars(
        select(RankingSnapshot)
        .where(RankingSnapshot.batch_ts == latest_ts)
        .order_by(RankingSnapshot.rank_position)
    ).all()
    if not rows:
        return None
    return [_row_to_sb(r) for r in rows]


def get_batch_meta(session: Session) -> dict | None:
    """Return metadata about the latest batch, or None."""
    latest_ts = session.scalar(
        select(RankingSnapshot.batch_ts)
        .order_by(RankingSnapshot.batch_ts.desc())
        .limit(1)
    )
    if latest_ts is None:
        return None

    count = session.scalar(
        select(func.count())
        .select_from(RankingSnapshot)
        .where(RankingSnapshot.batch_ts == latest_ts)
    ) or 0
    sample = session.scalar(
        select(RankingSnapshot)
        .where(RankingSnapshot.batch_ts == latest_ts)
        .limit(1)
    )
    age_hours = (datetime.now() - latest_ts).total_seconds() / 3600

    return {
        "batch_ts": latest_ts,
        "n_symbols": count,
        "min_bars": sample.min_bars_used if sample else 60,
        "age_hours": round(age_hours, 1),
    }


def clear_rankings(session: Session) -> int:
    """Delete all ranking batches. Returns deleted count."""
    try:
        result = session.execute(delete(RankingSnapshot))
        session.commit()
        return result.rowcount or 0
    except Exception:
        session.rollback()
        return 0


def format_age(age_hours: float) -> str:
    """Human-friendly age string in Persian."""
    if age_hours < 1:
        return f"{int(age_hours * 60)} دقیقه پیش"
    if age_hours < 24:
        return f"{age_hours:.1f} ساعت پیش"
    days = age_hours / 24
    return f"{days:.1f} روز پیش"