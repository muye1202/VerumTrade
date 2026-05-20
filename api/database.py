import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Use absolute path to the repository root to ensure the DB is always found
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SQLITE_DB_FILE = os.path.join(BASE_DIR, "trading_history.db")
DATABASE_URL = f"sqlite:///{SQLITE_DB_FILE}"

# Create the engine, configure for SQLite multi-threading
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

# Dependency to get DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_schema_migrations():
    """Apply incremental schema changes that SQLAlchemy create_all doesn't handle.

    create_all only creates missing tables; it never alters existing ones.
    Any new column added to a model must be migrated here explicitly.
    """
    from sqlalchemy import text
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(analysis_sessions)"))
        existing_columns = {row[1] for row in result}

        if "status" not in existing_columns:
            # Existing rows are all completed runs (saved only after finishing).
            conn.execute(text(
                "ALTER TABLE analysis_sessions ADD COLUMN status VARCHAR DEFAULT 'completed'"
            ))
            conn.commit()
