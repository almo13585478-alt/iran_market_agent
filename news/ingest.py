"""
News ingestion: manual add, CSV import, deduplication.
"""

import csv
from datetime import datetime
from io import StringIO
from pathlib import Path

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.audit import log_event
from database.models import NewsItem, NewsSymbolLink
from news.classifier import classify_news


def add_news(
    session: Session,
    title: str,
    body: str = "",
    source: str = "manual",
    source_url: str | None = None,
    published_at: datetime | None = None,
    credibility: int = 2,
) -> tuple[bool, str, int | None]:
    """
    Add one news item. Returns (created, message, news_id).
    Idempotent via hash.
    """
    if not title or not title.strip():
        return False, "عنوان خالی است", None

    cls = classify_news(session, title, body)

    existing = session.scalar(
        select(NewsItem).where(NewsItem.hash == cls["hash"])
    )
    if existing is not None:
        return False, "خبر تکراری (hash یکسان)", existing.id

    item = NewsItem(
        title=title.strip()[:400],
        body=(body or "").strip() or None,
        source=source,
        source_url=source_url,
        published_at=published_at or datetime.now(),
        category=cls["category"],
        sentiment_score=cls["sentiment_score"],
        impact=cls["impact"],
        credibility=credibility,
        hash=cls["hash"],
    )
    session.add(item)
    session.flush()

    for sym in cls["symbols"]:
        session.add(NewsSymbolLink(news_id=item.id, symbol=sym))

    session.commit()
    log_event(
        session, "system", "add_news",
        subject=",".join(cls["symbols"]) or "-",
        details=f"title={title[:60]} sentiment={cls['sentiment_score']} impact={cls['impact']}",
    )
    return True, (
        f"ذخیره شد — احساس {cls['sentiment_score']}, "
        f"تأثیر {cls['impact']}, نمادها {len(cls['symbols'])}"
    ), item.id


def import_csv(session: Session, csv_text: str) -> dict:
    """
    Import news from CSV text.
    Required columns: title
    Optional columns: body, source, source_url, published_at, credibility
    Returns {"added": int, "skipped": int, "errors": [..]}.
    """
    results = {"added": 0, "skipped": 0, "errors": []}
    try:
        reader = csv.DictReader(StringIO(csv_text))
        rows = list(reader)
    except Exception as e:
        results["errors"].append(f"CSV parse error: {e}")
        return results

    for idx, row in enumerate(rows, 1):
        try:
            title = (row.get("title") or "").strip()
            if not title:
                results["skipped"] += 1
                continue

            published = None
            if row.get("published_at"):
                try:
                    published = datetime.fromisoformat(
                        row["published_at"].strip()
                    )
                except ValueError:
                    pass

            cred = 2
            if row.get("credibility"):
                try:
                    cred = max(1, min(3, int(row["credibility"])))
                except ValueError:
                    pass

            created, msg, _ = add_news(
                session,
                title=title,
                body=row.get("body") or "",
                source=row.get("source") or "csv",
                source_url=row.get("source_url") or None,
                published_at=published,
                credibility=cred,
            )
            if created:
                results["added"] += 1
            else:
                results["skipped"] += 1
        except Exception as e:
            results["errors"].append(f"row {idx}: {e}")
    return results