"""
Wipe and reinitialize the database with the new schema.
Run:  python -m scripts.cleanup_db
"""
from pathlib import Path

from app.config import BASE_DIR
from database.init_db import init_database


def main():
    db = BASE_DIR / "data" / "market.db"
    if db.exists():
        backup = db.with_suffix(".db.old")
        db.replace(backup)
        print(f"Old DB backed up to {backup}")
    init_database()
    print(f"Fresh DB created at {db}")


if __name__ == "__main__":
    main()