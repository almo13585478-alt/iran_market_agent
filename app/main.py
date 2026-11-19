from app.logging_setup import setup_logging
from database.init_db import init_database
from loguru import logger


def main() -> None:
    setup_logging()
    init_database()
    logger.info("Phase 1 initialized successfully.")
    logger.info("Run the dashboard with:  python -m streamlit run dashboard/main.py")


if __name__ == "__main__":
    main()