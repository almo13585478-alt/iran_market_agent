"""
RSS adapter using feedparser. Feed URLs are user-provided (RssFeed table).

Design principle:
- Robust to broken feeds (timeouts, malformed XML)
- Idempotent (existing classifier dedupes via hash)
- Never blocks the rest of the app
"""

from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.audit import log_event
from database.models import RssFeed
from news.ingest import add_news


DEFAULT_TIMEOUT = 15


def _safe_parse_feed(url: str, parser=None):
    """Parse feed with feedparser, returning (entries, error_message)."""
    if parser is None:
        try:
            import feedparser
        except ImportError:
            return [], "feedparser نصب نیست — pip install feedparser"
        parser = feedparser.parse

    try:
        feed = parser(url)
    except Exception as e:
        return [], f"parse error: {e}"

    status = getattr(feed, "status", None)
    if status and status >= 400:
        return [], f"HTTP {status}"

    entries = getattr(feed, "entries", []) or []
    return entries, None


def _entry_to_news_fields(entry) -> dict | None:
    """Extract (title, body, published_at, url) from feedparser entry."""
    title = (getattr(entry, "title", "") or "").strip()
    if not title:
        return None

    body = (getattr(entry, "summary", "") or "").strip()
    if not body and hasattr(entry, "description"):
        body = (entry.description or "").strip()

    url = (getattr(entry, "link", "") or "").strip() or None

    published = None
    if hasattr(entry, "published_parsed") and entry.published_parsed:
        try:
            published = datetime(*entry.published_parsed[:6])
        except (TypeError, ValueError):
            pass
    if published is None:
        published = datetime.now()

    return {
        "title": title,
        "body": body,
        "source_url": url,
        "published_at": published,
    }


def fetch_feed(
    session: Session, feed: RssFeed, parser=None
) -> dict:
    """Fetch one feed, insert new items. Returns counts."""
    result = {"feed": feed.name, "ok": False, "new": 0, "skipped": 0, "error": None}

    entries, err = _safe_parse_feed(feed.url, parser=parser)
    if err:
        feed.is_healthy = False
        feed.last_error = err[:2000]
        feed.last_fetch_at = datetime.now()
        session.commit()
        result["error"] = err
        log_event(session, "system", "rss_fetch", feed.name,
                  err, success=False, commit=False)
        return result

    for entry in entries:
        fields = _entry_to_news_fields(entry)
        if fields is None:
            result["skipped"] += 1
            continue
        try:
            created, _, _ = add_news(
                session,
                title=fields["title"],
                body=fields["body"],
                source=f"rss:{feed.name}",
                source_url=fields["source_url"],
                published_at=fields["published_at"],
                credibility=2,
            )
            if created:
                result["new"] += 1
            else:
                result["skipped"] += 1
        except Exception as e:
            logger.exception("RSS insert failed for {}", fields["title"][:50])
            result["skipped"] += 1

    feed.is_healthy = True
    feed.last_error = None
    feed.last_fetch_at = datetime.now()
    session.commit()

    log_event(session, "system", "rss_fetch", feed.name,
              f"new={result['new']} skipped={result['skipped']}",
              commit=False)
    result["ok"] = True
    return result


def fetch_all_active(session: Session, parser=None) -> list[dict]:
    feeds = session.scalars(
        select(RssFeed).where(RssFeed.is_active.is_(True))
    ).all()
    return [fetch_feed(session, f, parser=parser) for f in feeds]


def add_feed(session: Session, name: str, url: str) -> tuple[bool, str]:
    name = (name or "").strip()
    url = (url or "").strip()
    if not name or not url:
        return False, "نام و URL الزامی است"
    existing = session.scalar(select(RssFeed).where(RssFeed.name == name))
    if existing is not None:
        return False, f"فید با نام «{name}» از قبل هست"
    session.add(RssFeed(name=name, url=url))
    session.commit()
    return True, "فید اضافه شد"


def remove_feed(session: Session, feed_id: int) -> bool:
    feed = session.scalar(select(RssFeed).where(RssFeed.id == feed_id))
    if feed is None:
        return False
    session.delete(feed)
    session.commit()
    return True