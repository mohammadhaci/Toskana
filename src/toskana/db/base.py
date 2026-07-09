"""SQLAlchemy 2.x engine/session factories and declarative base.

SQLite is configured for durability and correctness on every connection:
- ``journal_mode=WAL`` — concurrent readers with a single writer.
- ``foreign_keys=ON`` — enforce FK constraints (off by default in SQLite).
- ``synchronous=NORMAL`` — safe with WAL, much faster than FULL.
"""

from __future__ import annotations

import os
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from toskana.config import sqlite_url


class Base(DeclarativeBase):
    """Declarative base for all Toskana ORM models."""


def _set_sqlite_pragmas(dbapi_connection: Any, connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
    finally:
        cursor.close()


def make_engine(db_path: str | os.PathLike[str], *, echo: bool = False) -> Engine:
    """Create a SQLite engine with WAL + foreign-key pragmas applied per connection.

    ``db_path`` may be a filesystem path or ``":memory:"`` for tests.
    """
    engine = create_engine(sqlite_url(db_path), echo=echo)
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return engine


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Create a sessionmaker bound to the given engine."""
    return sessionmaker(bind=engine, expire_on_commit=False)
