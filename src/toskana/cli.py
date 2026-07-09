"""Toskana command-line interface (stdlib argparse, no ML imports)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from toskana import __version__
from toskana.config import AppConfig, load_config


def _repo_root() -> Path:
    """Locate the directory containing alembic.ini (editable install / checkout)."""
    candidates = [
        Path(__file__).resolve().parents[2],  # <root>/src/toskana/cli.py -> <root>
        Path.cwd(),
    ]
    for candidate in candidates:
        if (candidate / "alembic.ini").is_file():
            return candidate
    raise FileNotFoundError("alembic.ini not found; run from the Toskana checkout or set cwd to it")


def _alembic_upgrade_head(config: AppConfig) -> None:
    from alembic.config import Config as AlembicConfig

    from alembic import command

    root = _repo_root()
    alembic_cfg = AlembicConfig(str(root / "alembic.ini"))
    alembic_cfg.set_main_option("script_location", str(root / "alembic"))
    alembic_cfg.set_main_option("sqlalchemy.url", config.db_url)
    command.upgrade(alembic_cfg, "head")


def cmd_init_db(config: AppConfig) -> int:
    db_path = Path(config.db_path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    _alembic_upgrade_head(config)
    print(f"Database ready at {config.db_path} (alembic head)")
    return 0


def cmd_seed(config: AppConfig) -> int:
    from toskana.db.base import make_engine, make_session_factory
    from toskana.db.seed import seed

    if not Path(config.db_path).exists():
        print(f"Database {config.db_path} does not exist — run `toskana init-db` first")
        return 1
    engine = make_engine(config.db_path)
    session_factory = make_session_factory(engine)
    with session_factory() as session:
        restaurant = seed(session)
    print(f"Seeded restaurant '{restaurant.name}' (slug={restaurant.slug})")
    return 0


def cmd_run(config: AppConfig) -> int:
    print(
        f"toskana run: server not implemented until M4 "
        f"(would listen on {config.host}:{config.port})"
    )
    return 0


def cmd_simulate(config: AppConfig) -> int:
    print("toskana simulate: not implemented until M3")
    return 0


def cmd_eval_counting(config: AppConfig) -> int:
    print("toskana eval-counting: not implemented until M10")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="toskana",
        description="Toskana — pass-through counter (AI vision line-crossing counts).",
    )
    parser.add_argument("--version", action="version", version=f"toskana {__version__}")
    parser.add_argument(
        "-c",
        "--config",
        default=None,
        help="Path to config YAML (default: $TOSKANA_CONFIG or ./config.yaml)",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init-db", help="Create/upgrade the database schema (alembic upgrade head)")
    sub.add_parser("seed", help="Insert idempotent demo data (restaurant, cameras, mappings)")
    sub.add_parser("run", help="Start the dashboard server (stub until M4)")
    sub.add_parser("simulate", help="Run counting pipeline on a video file (stub until M3)")
    sub.add_parser("eval-counting", help="Evaluate counts against ground truth (stub until M10)")
    return parser


COMMANDS = {
    "init-db": cmd_init_db,
    "seed": cmd_seed,
    "run": cmd_run,
    "simulate": cmd_simulate,
    "eval-counting": cmd_eval_counting,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    return COMMANDS[args.command](config)


if __name__ == "__main__":
    sys.exit(main())
