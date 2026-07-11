"""Apply Alembic migrations programmatically.

Used by ``toskana init-db`` and by the dashboard at startup (``toskana
run``). Auto-upgrading on startup means that after a ``git pull`` adds a
new column or table, the operator never has to remember ``init-db`` — the
running app brings its own SQLite file up to date and the new schema is
simply there. Local-first deployments update in place, so an in-process
migration is the least surprising behaviour.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from sqlalchemy import inspect

from toskana.config import AppConfig
from toskana.db.base import make_engine

if TYPE_CHECKING:
    from alembic.config import Config as AlembicConfig

logger = logging.getLogger(__name__)


def find_alembic_root() -> Path | None:
    """Locate the directory holding ``alembic.ini`` (editable install / checkout)."""
    candidates = [
        Path(__file__).resolve().parents[3],  # <root>/src/toskana/db/migrate.py -> <root>
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / "alembic.ini").is_file():
            return candidate
    return None


def _alembic_config(root: Path, config: AppConfig) -> AlembicConfig:
    from alembic.config import Config as AlembicConfig

    alembic_cfg = AlembicConfig(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", config.db_url)
    return alembic_cfg


def upgrade_to_head(config: AppConfig) -> None:
    """Bring the configured database up to the latest Alembic revision.

    * A fresh (or Alembic-managed) database is upgraded to ``head``,
      creating or altering tables as the revision chain dictates.
    * A database whose tables were created directly from the ORM metadata
      (``Base.metadata.create_all`` — used by the test suite and by
      ``simulate``) has no ``alembic_version`` stamp but already matches
      the ORM head; it is left untouched so we never try to re-create
      existing tables.

    Raises :class:`FileNotFoundError` if ``alembic.ini`` cannot be located.
    """
    root = find_alembic_root()
    if root is None:
        raise FileNotFoundError(
            "alembic.ini not found; run from the Toskana checkout or set cwd to it"
        )

    db_path = Path(config.db_path)
    if config.db_path != ":memory:" and db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)

    engine = make_engine(config.db_path)
    try:
        table_names = set(inspect(engine).get_table_names())
    finally:
        engine.dispose()

    has_version = "alembic_version" in table_names
    app_tables = table_names - {"alembic_version"}
    if not has_version and app_tables:
        # Schema built from ORM metadata, already at head: nothing to do.
        return

    from alembic import command

    command.upgrade(_alembic_config(root, config), "head")
