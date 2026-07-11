"""Tests for the programmatic Alembic upgrade helper (``toskana.db.migrate``).

These lock in the behaviour that lets ``toskana run`` self-heal an older
database: after a ``git pull`` adds columns/tables, starting the app must
bring an existing (Alembic-stamped) SQLite file up to head without the
operator remembering ``init-db`` — while a schema created straight from the
ORM metadata (the test/``simulate`` path) is left untouched.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import inspect

from toskana.config import AppConfig
from toskana.db.base import Base, make_engine
from toskana.db.migrate import _alembic_config, find_alembic_root, upgrade_to_head

INITIAL_REVISION = "500e6e3fa5c4"


def _stamp_and_upgrade_to(config: AppConfig, revision: str) -> None:
    from alembic import command

    root = find_alembic_root()
    assert root is not None
    command.upgrade(_alembic_config(root, config), revision)


def test_upgrade_brings_old_stamped_db_to_head(tmp_path: Path) -> None:
    # Simulate the user's machine: a DB created by an older `init-db` that
    # only knows the initial revision (no refiner / detector_backend / settings).
    config = AppConfig(db_path=str(tmp_path / "old.db"))
    _stamp_and_upgrade_to(config, INITIAL_REVISION)

    engine = make_engine(config.db_path)
    try:
        before = inspect(engine)
        assert "app_settings" not in before.get_table_names()
        assert "refined" not in {c["name"] for c in before.get_columns("events")}
    finally:
        engine.dispose()

    upgrade_to_head(config)

    engine = make_engine(config.db_path)
    try:
        after = inspect(engine)
        assert "app_settings" in after.get_table_names()
        assert "refined" in {c["name"] for c in after.get_columns("events")}
        assert "detector_backend" in {c["name"] for c in after.get_columns("cameras")}
    finally:
        engine.dispose()


def test_upgrade_creates_fresh_database(tmp_path: Path) -> None:
    config = AppConfig(db_path=str(tmp_path / "sub" / "fresh.db"))  # parent dir missing
    upgrade_to_head(config)

    engine = make_engine(config.db_path)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert {"restaurants", "cameras", "events", "app_settings", "alembic_version"} <= names


def test_upgrade_skips_create_all_schema(tmp_path: Path) -> None:
    # A DB built from ORM metadata (tests / `simulate`) is already at head but
    # carries no alembic stamp; upgrade_to_head must not try to re-create it.
    config = AppConfig(db_path=str(tmp_path / "orm.db"))
    engine = make_engine(config.db_path)
    Base.metadata.create_all(engine)
    engine.dispose()

    upgrade_to_head(config)  # must not raise "table already exists"

    engine = make_engine(config.db_path)
    try:
        names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()
    assert "alembic_version" not in names  # left untouched
    assert "app_settings" in names
