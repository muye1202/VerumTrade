import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# Define the local database file
SQLITE_DB_FILE = "trading_history.db"
DATABASE_URL = f"sqlite:///./{SQLITE_DB_FILE}"

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
