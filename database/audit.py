"""Helper to append audit-log rows."""

from sqlalchemy.orm import Session
from database.models import AuditLog
from loguru import logger


def log_event(
    session: Session,
    category: str,
    action: str,
    subject: str | None = None,
    details: str | None = None,
    success: bool = True,
    commit: bool = True,
) -> None:
    try:
        session.add(AuditLog(
            category=category, action=action, subject=subject,
            details=details, success=success,
        ))
        if commit:
            session.commit()
    except Exception:
        logger.exception("Audit log write failed")