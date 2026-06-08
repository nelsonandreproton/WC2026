"""Engine and session factory.

Per project rules: expire_on_commit=False, SQLite, PRAGMA foreign_keys=ON so
ON DELETE CASCADE actually fires (SQLite has FKs off by default).
"""

from __future__ import annotations

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from wc2026bot.db.models import Base


@event.listens_for(Engine, "connect")
def _enable_sqlite_fk(dbapi_connection, _connection_record) -> None:
    """Enable foreign key enforcement on every SQLite connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.close()


def make_engine(db_path: str, echo: bool = False) -> Engine:
    """Create an engine for the given SQLite file path (or ':memory:')."""
    url = f"sqlite:///{db_path}"
    return create_engine(url, echo=echo, future=True)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


def init_db(engine: Engine) -> None:
    """Create all tables. Idempotent."""
    Base.metadata.create_all(engine)
