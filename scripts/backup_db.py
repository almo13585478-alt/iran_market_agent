"""
Backup utility: copy the SQLite file + export key user data to JSON.

Backups go to data/backups/. Old backups are pruned to keep the last N.
"""

import json
import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import select

from app.config import BASE_DIR
from database.session import get_session
from database.models import (
    AuditLog, CodalReport, CodalWatch, Fundamental, MacroIndicator,
    MarketSnapshot, NewsItem, NewsSymbolLink, PaperSignal,
    PortfolioTransaction, RssFeed,
)


BACKUP_DIR = BASE_DIR / "data" / "backups"


USER_DATA_TABLES = [
    ("portfolio_transactions", PortfolioTransaction),
    ("paper_signals", PaperSignal),
    ("fundamentals", Fundamental),
    ("macro_indicators", MacroIndicator),
    ("market_snapshots", MarketSnapshot),
    ("news_items", NewsItem),
    ("news_symbol_links", NewsSymbolLink),
    ("rss_feeds", RssFeed),
    ("codal_watch", CodalWatch),
    ("codal_reports", CodalReport),
    ("audit_log", AuditLog),
]


def _serialize_value(v):
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _row_to_dict(row) -> dict:
    return {
        c.name: _serialize_value(getattr(row, c.name))
        for c in row.__table__.columns
    }


def full_db_backup() -> tuple[bool, str]:
    """Copy the .db file to backups/ with timestamp."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    src = BASE_DIR / "data" / "market.db"
    if not src.exists():
        return False, "دیتابیس پیدا نشد. اول init_database را اجرا کنید."
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    dest = BACKUP_DIR / f"market_{ts}.db"
    shutil.copy2(src, dest)
    size_mb = dest.stat().st_size / (1024 * 1024)
    return True, f"{dest.name} ({size_mb:.1f} MB)"


def export_json() -> tuple[bool, str, dict]:
    """Export all user data tables to a JSON file."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    s = get_session()
    try:
        data = {
            "exported_at": datetime.now().isoformat(),
            "app_version": "1.0",
            "tables": {},
        }
        counts = {}
        for name, model in USER_DATA_TABLES:
            try:
                rows = s.scalars(select(model)).all()
                data["tables"][name] = [_row_to_dict(r) for r in rows]
                counts[name] = len(rows)
            except Exception as e:
                data["tables"][name] = []
                counts[name] = f"error: {e}"

        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = BACKUP_DIR / f"userdata_{ts}.json"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        size_kb = path.stat().st_size / 1024
        return True, f"{path.name} ({size_kb:.1f} KB)", counts
    finally:
        s.close()


def list_backups() -> list[dict]:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    out = []
    for p in sorted(BACKUP_DIR.iterdir(), reverse=True):
        if not p.is_file():
            continue
        stat = p.stat()
        out.append({
            "name": p.name,
            "path": str(p),
            "size_kb": round(stat.st_size / 1024, 1),
            "modified": datetime.fromtimestamp(stat.st_mtime),
            "kind": "db" if p.suffix == ".db" else "json",
        })
    return out


def cleanup_old_backups(keep_last_n: int = 10) -> int:
    """Delete oldest backups beyond keep_last_n. Returns count deleted."""
    backups = list_backups()
    if len(backups) <= keep_last_n:
        return 0
    to_delete = backups[keep_last_n:]
    deleted = 0
    for b in to_delete:
        try:
            Path(b["path"]).unlink()
            deleted += 1
        except Exception:
            pass
    return deleted


def delete_backup(name: str) -> bool:
    path = BACKUP_DIR / name
    if not path.exists():
        return False
    path.unlink()
    return True