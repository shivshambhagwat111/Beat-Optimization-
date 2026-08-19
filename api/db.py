from typing import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from config import DATABASE_URL

engine = create_engine(DATABASE_URL, pool_pre_ping=True)

if engine.dialect.name == "sqlite":
    # SQLite's default rollback-journal mode takes a whole-file lock while a
    # write transaction is open, so the optimize background task (many INSERTs
    # before its final commit) collides with the frontend's concurrent polling
    # GETs on a separate connection, raising "database is locked". WAL mode lets
    # readers and writers proceed concurrently instead of blocking each other,
    # and a generous busy_timeout makes any remaining writer-vs-writer
    # contention retry instead of failing immediately. Not needed/valid on
    # Postgres, which has proper MVCC.
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=30000")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()


def init_db() -> None:
    """Creates all tables registered on Base.metadata if they don't already exist."""
    import api.models  # noqa: F401  (ensures models are registered before create_all)

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a request-scoped SQLAlchemy session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
