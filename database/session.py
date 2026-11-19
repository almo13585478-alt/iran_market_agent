from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from app.config import settings, BASE_DIR


# Ensure data directory exists (for sqlite:///data/market.db)
(BASE_DIR / "data").mkdir(parents=True, exist_ok=True)

_connect_args = {}
if settings.DB_URL.startswith("sqlite"):
    _connect_args = {"check_same_thread": False}

engine = create_engine(
    settings.DB_URL,
    echo=False,
    future=True,
    connect_args=_connect_args,
)

SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
    expire_on_commit=False,
)


def get_session() -> Session:
    return SessionLocal()