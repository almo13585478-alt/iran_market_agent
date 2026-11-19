"""
Settings manager: typed get/set with DB persistence and safe fallbacks.

Rules:
- If DB unavailable, returns defaults (never crashes)
- Values are JSON-encoded for type safety
- get_all() merges DB values over defaults
"""

import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session


SETTINGS_DEFAULTS = {
    # Trading
    "portfolio_value": 1_000_000_000,   # IRR
    "risk_pct": 0.5,                    # % per trade
    "min_rr": 2.0,                      # minimum R/R
    # Paper scan defaults
    "paper_min_soft": 5,
    "paper_atr_mult": 2.0,
    "paper_rr_ratio": 2.0,
    # Data
    "default_scan_symbols": 30,
    # UI
    "theme": "روشن",
}


SETTINGS_LABELS = {
    "portfolio_value": ("💰 ارزش سبد (ریال)", "int"),
    "risk_pct": ("📊 ریسک هر معامله (٪)", "float"),
    "min_rr": ("⚖️ حداقل R/R", "float"),
    "paper_min_soft": ("📝 حداقل فیلتر Paper", "int"),
    "paper_atr_mult": ("📝 ATR× Paper", "float"),
    "paper_rr_ratio": ("📝 R/R Paper", "float"),
    "default_scan_symbols": ("🔢 تعداد نماد پیش‌فرض تحلیل", "int"),
    "theme": ("🎨 تم", "str"),
}


def _safe_json_decode(value: str):
    try:
        return json.loads(value)
    except Exception:
        return value


def get_setting(session: Session | None, key: str, default=None):
    """Read one setting. Falls back to default or SETTINGS_DEFAULTS."""
    fallback = default if default is not None else SETTINGS_DEFAULTS.get(key)
    if session is None:
        return fallback
    try:
        from database.models import AppSettings
        row = session.scalar(
            select(AppSettings).where(AppSettings.key == key)
        )
        if row is None:
            return fallback
        return _safe_json_decode(row.value)
    except Exception:
        return fallback


def set_setting(session: Session, key: str, value) -> bool:
    """Write one setting. Returns True on success."""
    try:
        from database.models import AppSettings
        row = session.scalar(
            select(AppSettings).where(AppSettings.key == key)
        )
        encoded = json.dumps(value, ensure_ascii=False)
        if row is None:
            row = AppSettings(key=key, value=encoded)
            session.add(row)
        else:
            row.value = encoded
            row.updated_at = datetime.now()
        session.commit()
        return True
    except Exception:
        return False


def get_all(session: Session | None) -> dict:
    """Return all settings merged over defaults."""
    out = dict(SETTINGS_DEFAULTS)
    if session is None:
        return out
    try:
        from database.models import AppSettings
        for row in session.scalars(select(AppSettings)).all():
            out[row.key] = _safe_json_decode(row.value)
    except Exception:
        pass
    return out


def save_all(session: Session, values: dict) -> dict:
    """Save multiple settings. Returns {key: success}."""
    results = {}
    for k, v in values.items():
        results[k] = set_setting(session, k, v)
    return results


def reset_to_defaults(session: Session) -> dict:
    """Reset all settings to defaults. Returns save results."""
    return save_all(session, SETTINGS_DEFAULTS)


def cast_value(key: str, raw):
    """Cast raw input to the correct type based on SETTINGS_LABELS."""
    if key not in SETTINGS_LABELS:
        return raw
    _, type_hint = SETTINGS_LABELS[key]
    try:
        if type_hint == "int":
            return int(float(raw))
        if type_hint == "float":
            return float(raw)
        return str(raw)
    except Exception:
        return SETTINGS_DEFAULTS.get(key, raw)