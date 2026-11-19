from loguru import logger
from database.models import Base
from database.session import engine


def init_database() -> None:
    Base.metadata.create_all(bind=engine)
    logger.info("Database schema ensured at {url}", url=engine.url)


if __name__ == "__main__":
    init_database()