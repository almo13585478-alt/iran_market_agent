from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    APP_NAME: str = "Iran Market Agent"
    APP_VERSION: str = "0.1.0"
    ENV: str = "dev"

    DB_URL: str = "sqlite:///data/market.db"

    LOG_LEVEL: str = "INFO"
    LOG_DIR: str = str(BASE_DIR / "logs")

    # Risk parameters (used later)
    RISK_PER_TRADE_PCT: float = 0.5
    MAX_TOTAL_RISK_PCT: float = 1.5
    MAX_OPEN_POSITIONS: int = 3
    MIN_RR_RATIO: float = 2.0


settings = Settings()