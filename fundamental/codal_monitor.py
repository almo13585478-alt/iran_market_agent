"""
Codal monitoring (best effort).

Design:
- User provides a Codal search URL per symbol (once).
- Scheduler fetches each URL daily, extracts report links.
- New URLs are recorded in CodalReport table.
- We do NOT auto-fetch report bodies (user reviews each manually).
- If a URL is broken, watch entry is marked unhealthy; clear error shown.
"""

import re
from datetime import datetime
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup
from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from database.audit import log_event
from database.models import CodalReport, CodalWatch


USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)


REPORT_URL_RE = re.compile(
    r"(https?://[^\s\"'<>]*codal[^\s\"'<>]*(?:Report|Decision|Letter)[^\s\"'<>]*)",
    re.IGNORECASE,
)


def _normalize_search_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return url
    if not url.startswith(("http://", "https://")):
        return "https://" + url
    return url


def _extract_report_links(html: str, base_url: str) -> list[tuple[str, str]]:
    """Return [(absolute_url, title), ...] for Codal report links."""
    results: list[tuple[str, str]] = []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        try:
            soup = BeautifulSoup(html, "html.parser")
        except Exception:
            return results

    for a in soup.find_all("a", href=True):
        href = a["href"]
        full = urljoin(base_url, href)
        if not REPORT_URL_RE.search(full):
            # fallback heuristic
            if "codal.ir" in full and ("Report" in full or "Decision" in full
                                       or "LetterSerial" in full):
                pass
            else:
                continue
        title = (a.get_text() or "").strip() or full.split("/")[-1][:100]
        results.append((full, title[:300]))

    # Also scan raw text for URLs (some pages load links via JS-injected HTML)
    for m in REPORT_URL_RE.finditer(html):
        url = m.group(1)
        if not any(url == r[0] for r in results):
            results.append((url, url.split("/")[-1][:100]))

    # Deduplicate by URL
    seen = set()
    unique = []
    for url, title in results:
        if url in seen:
            continue
        seen.add(url)
        unique.append((url, title))
    return unique


def check_watch(session: Session, watch: CodalWatch) -> dict:
    """Fetch a watch URL, insert new reports, return summary."""
    result = {"symbol": watch.symbol, "ok": False, "new": 0, "total_links": 0,
              "error": None}

    url = _normalize_search_url(watch.search_url)
    if not url:
        watch.is_healthy = False
        watch.last_error = "URL خالی است"
        watch.last_check_at = datetime.now()
        session.commit()
        result["error"] = watch.last_error
        return result

    try:
        with httpx.Client(
            timeout=25.0,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
                "Accept-Language": "fa-IR,fa;q=0.9,en;q=0.8",
                "Referer": "https://www.codal.ir/",
            },
            follow_redirects=True,
        ) as c:
            r = c.get(url)
            r.raise_for_status()
            html = r.text
    except httpx.HTTPStatusError as e:
        code = getattr(r, "status_code", None) or (
            getattr(e.response, "status_code", None)
            if getattr(e, "response", None) else None
        )
        watch.is_healthy = False
        watch.last_error = f"HTTP {code}" if code else "HTTP error"
        watch.last_check_at = datetime.now()
        watch.new_since_check = 0
        session.commit()
        result["error"] = watch.last_error
        log_event(session, "system", "codal_watch", watch.symbol,
                  watch.last_error, success=False, commit=False)
        return result
    except Exception as e:
        watch.is_healthy = False
        watch.last_error = f"خطای شبکه: {e}"[:500]
        watch.last_check_at = datetime.now()
        watch.new_since_check = 0
        session.commit()
        result["error"] = watch.last_error
        return result

    links = _extract_report_links(html, url)
    result["total_links"] = len(links)

    new_count = 0
    for link_url, title in links:
        existing = session.scalar(
            select(CodalReport).where(CodalReport.url == link_url)
        )
        if existing is not None:
            continue
        session.add(CodalReport(
            watch_id=watch.id,
            symbol=watch.symbol,
            title=title[:400],
            url=link_url[:500],
        ))
        new_count += 1

    watch.is_healthy = True
    watch.last_error = None
    watch.last_check_at = datetime.now()
    watch.new_since_check = new_count
    session.commit()

    result["ok"] = True
    result["new"] = new_count
    log_event(session, "system", "codal_watch", watch.symbol,
              f"links={len(links)} new={new_count}", commit=False)
    return result


def check_all_active(session: Session) -> list[dict]:
    watches = session.scalars(
        select(CodalWatch).where(CodalWatch.is_active.is_(True))
    ).all()
    return [check_watch(session, w) for w in watches]


def add_watch(
    session: Session, symbol: str, search_url: str, company_name: str | None = None,
) -> tuple[bool, str]:
    symbol = (symbol or "").strip()
    search_url = (search_url or "").strip()
    if not symbol or not search_url:
        return False, "نماد و URL الزامی است"
    existing = session.scalar(
        select(CodalWatch).where(CodalWatch.symbol == symbol)
    )
    if existing is not None:
        return False, f"نماد «{symbol}» از قبل در watch است"
    session.add(CodalWatch(
        symbol=symbol, company_name=company_name or symbol,
        search_url=search_url,
    ))
    session.commit()
    return True, f"نماد {symbol} به watch اضافه شد"


def remove_watch(session: Session, watch_id: int) -> bool:
    w = session.scalar(select(CodalWatch).where(CodalWatch.id == watch_id))
    if w is None:
        return False
    session.delete(w)
    session.commit()
    return True


def unprocessed_reports(session: Session, limit: int = 100) -> list[CodalReport]:
    return session.scalars(
        select(CodalReport)
        .where(CodalReport.is_processed.is_(False))
        .order_by(CodalReport.discovered_at.desc())
        .limit(limit)
    ).all()


def mark_processed(session: Session, report_id: int) -> bool:
    r = session.scalar(select(CodalReport).where(CodalReport.id == report_id))
    if r is None:
        return False
    r.is_processed = True
    session.commit()
    return True