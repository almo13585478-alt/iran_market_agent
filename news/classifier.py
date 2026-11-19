"""
Keyword-based news classifier. Honest and transparent — no LLM.
Returns (sentiment_score, impact, category, symbol_matches).
"""

import hashlib
import re
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from database.models import Instrument
from news.keywords import (
    NEGATIVE_HIGH, NEGATIVE_MEDIUM, POSITIVE_HIGH, POSITIVE_MEDIUM,
    CATEGORY_HINTS, normalize_persian,
)


def _find_symbols(text: str, symbol_map: dict[str, str]) -> list[str]:
    """
    Find symbols mentioned in text. symbol_map is {normalized_symbol: original_symbol}.
    Uses simple word-boundary check for Persian.
    """
    found: set[str] = set()
    for norm_sym, orig in symbol_map.items():
        if len(norm_sym) < 2:
            continue
        # match symbol preceded/followed by non-letter or string boundary
        pattern = r"(?<![\wآ-ی])" + re.escape(norm_sym) + r"(?![\wآ-ی])"
        if re.search(pattern, text):
            found.add(orig)
    return sorted(found)


def _score_and_category(text: str) -> tuple[float, str, str]:
    """Return (sentiment_score, impact, category)."""
    # Count matches
    nh = sum(1 for k in NEGATIVE_HIGH if k in text)
    nm = sum(1 for k in NEGATIVE_MEDIUM if k in text)
    ph = sum(1 for k in POSITIVE_HIGH if k in text)
    pm = sum(1 for k in POSITIVE_MEDIUM if k in text)

    # Weighted sentiment
    pos = ph * 2.0 + pm * 1.0
    neg = nh * 2.5 + nm * 1.0
    total = pos + neg

    if total == 0:
        sentiment = 0.0
    else:
        sentiment = (pos - neg) / total
    sentiment = max(-1.0, min(1.0, round(sentiment, 2)))

    # Impact
    if nh >= 1 or ph >= 2:
        impact = "high"
    elif nm >= 2 or pm >= 2:
        impact = "medium"
    else:
        impact = "low"

    # Category
    category = "other"
    best_hits = 0
    for cat, hints in CATEGORY_HINTS.items():
        hits = sum(1 for h in hints if h in text)
        if hits > best_hits:
            best_hits = hits
            category = cat

    return sentiment, impact, category


def classify_news(
    session: Session,
    title: str,
    body: str = "",
) -> dict:
    """
    Return dict with sentiment_score, impact, category, symbols, hash.
    """
    title_norm = normalize_persian(title or "")
    body_norm = normalize_persian(body or "")
    combined = title_norm + " " + body_norm

    sentiment, impact, category = _score_and_category(combined)

    # Build symbol map once (cached per call; small N in practice)
    insts = session.scalars(
        select(Instrument).where(Instrument.is_active.is_(True))
    ).all()
    symbol_map = {normalize_persian(i.symbol): i.symbol for i in insts}
    symbols = _find_symbols(combined, symbol_map)

    digest = hashlib.sha256(
        (title_norm.strip() + "|" + body_norm[:200]).encode("utf-8")
    ).hexdigest()[:32]

    return {
        "sentiment_score": sentiment,
        "impact": impact,
        "category": category,
        "symbols": symbols,
        "hash": digest,
    }