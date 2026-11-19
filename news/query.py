"""
Query helpers used by signal generator and reports.
"""

from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.orm import Session

from database.models import NewsItem, NewsSymbolLink


def has_recent_negative_news(
    session: Session,
    symbol: str,
    days: int = 3,
    sentiment_threshold: float = -0.4,
    min_impact: str = "medium",
) -> bool:
    """Return True if symbol has a strong negative news in last N days."""
    since = datetime.now() - timedelta(days=days)
    impacts = {"low": ["low", "medium", "high"],
               "medium": ["medium", "high"],
               "high": ["high"]}
    allowed = impacts.get(min_impact, ["medium", "high"])

    cnt = session.scalar(
        select(func.count(NewsItem.id))
        .join(NewsSymbolLink, NewsSymbolLink.news_id == NewsItem.id)
        .where(NewsSymbolLink.symbol == symbol)
        .where(NewsItem.published_at >= since)
        .where(NewsItem.sentiment_score <= sentiment_threshold)
        .where(NewsItem.impact.in_(allowed))
    ) or 0
    return cnt > 0


def negative_symbols_today(
    session: Session, days: int = 3
) -> list[dict]:
    """Return [{symbol, count, worst_sentiment, latest_title}]."""
    since = datetime.now() - timedelta(days=days)
    rows = session.execute(
        select(
            NewsSymbolLink.symbol,
            func.count(NewsItem.id).label("cnt"),
            func.min(NewsItem.sentiment_score).label("worst"),
        )
        .join(NewsItem, NewsItem.id == NewsSymbolLink.news_id)
        .where(NewsItem.published_at >= since)
        .where(NewsItem.sentiment_score < 0)
        .group_by(NewsSymbolLink.symbol)
        .order_by(func.min(NewsItem.sentiment_score))
    ).all()

    out = []
    for sym, cnt, worst in rows:
        latest = session.execute(
            select(NewsItem.title)
            .join(NewsSymbolLink, NewsSymbolLink.news_id == NewsItem.id)
            .where(NewsSymbolLink.symbol == sym)
            .where(NewsItem.published_at >= since)
            .where(NewsItem.sentiment_score < 0)
            .order_by(NewsItem.published_at.desc())
            .limit(1)
        ).first()
        out.append({
            "symbol": sym,
            "count": int(cnt),
            "worst_sentiment": float(worst),
            "latest_title": latest[0] if latest else "-",
        })
    return out


def recent_news(
    session: Session,
    limit: int = 100,
    category: str | None = None,
    impact: str | None = None,
    symbol: str | None = None,
) -> list[NewsItem]:
    q = select(NewsItem).order_by(NewsItem.published_at.desc())
    if category:
        q = q.where(NewsItem.category == category)
    if impact:
        q = q.where(NewsItem.impact == impact)
    if symbol:
        q = q.join(NewsSymbolLink, NewsSymbolLink.news_id == NewsItem.id).where(
            NewsSymbolLink.symbol == symbol
        )
    return session.scalars(q.limit(limit)).all()