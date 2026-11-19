import sys
from pathlib import Path
from loguru import logger
from app.config import settings


def setup_logging() -> None:
    Path(settings.LOG_DIR).mkdir(parents=True, exist_ok=True)

    logger.remove()
    logger.add(
        sys.stdout,
        level=settings.LOG_LEVEL,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan> - "
            "<level>{message}</level>"
        ),
    )
    logger.add(
        str(Path(settings.LOG_DIR) / "app_{time:YYYY-MM-DD}.log"),
        rotation="10 MB",
        retention="14 days",
        level=settings.LOG_LEVEL,
        encoding="utf-8",
    )