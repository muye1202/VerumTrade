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
