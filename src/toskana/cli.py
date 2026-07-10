"""Toskana command-line interface (stdlib argparse, no ML imports)."""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

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


def cmd_init_db(config: AppConfig, args: argparse.Namespace) -> int:
    db_path = Path(config.db_path)
    if db_path.parent and not db_path.parent.exists():
        db_path.parent.mkdir(parents=True, exist_ok=True)
    _alembic_upgrade_head(config)
    print(f"Database ready at {config.db_path} (alembic head)")
    return 0


def cmd_seed(config: AppConfig, args: argparse.Namespace) -> int:
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


def cmd_run(config: AppConfig, args: argparse.Namespace) -> int:
    """Start the dashboard server (REST + WS + MJPEG + camera pipelines)."""
    import uvicorn

    from toskana.api.app import create_app

    host = args.host if args.host is not None else config.host
    port = args.port if args.port is not None else config.port
    app = create_app(config, start_pipelines=not args.no_pipeline)
    mode = "API only (--no-pipeline)" if args.no_pipeline else "with camera pipelines"
    print(f"Toskana dashboard: http://{host}:{port}/  [{mode}]")
    uvicorn.run(app, host=host, port=port, log_level=config.log_level.lower())
    return 0


def _parse_line_arg(value: str) -> tuple[float, float, float, float]:
    parts = value.split(",")
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--line expects x1,y1,x2,y2 (normalized 0..1)")
    try:
        coords = tuple(float(p) for p in parts)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"--line has a non-numeric coordinate: {value!r}") from exc
    if not all(0.0 <= c <= 1.0 for c in coords):
        raise argparse.ArgumentTypeError("--line coordinates must be normalized to 0..1")
    x1, y1, x2, y2 = coords
    return (x1, y1, x2, y2)


def _simulate_db_context(
    db_path: str, config: AppConfig, video: Path, backend: str
) -> tuple[int, int, list[Any]]:
    """Prepare a DB for persistence: schema + simulate restaurant/camera rows.

    Returns ``(restaurant_id, camera_id, mapping_rules)``.
    """
    from sqlalchemy import select

    from toskana.db.base import Base, make_engine, make_session_factory
    from toskana.db.models import Camera, Category, ClassMapping, Restaurant
    from toskana.vision.backends.synthetic import SYNTHETIC_CLASS_NAMES
    from toskana.vision.mapping import MappingRule

    engine = make_engine(db_path)
    Base.metadata.create_all(engine)  # no-op on an initialized database
    rules: list[Any] = []
    with make_session_factory(engine)() as session:
        restaurant = session.scalar(
            select(Restaurant).where(Restaurant.slug == config.active_restaurant_slug)
        )
        if restaurant is None:
            restaurant = Restaurant(
                slug=config.active_restaurant_slug, name=config.active_restaurant_slug
            )
            session.add(restaurant)
            session.flush()
        camera = session.scalar(
            select(Camera).where(Camera.restaurant_id == restaurant.id, Camera.name == "Simulate")
        )
        if camera is None:
            camera = Camera(
                restaurant_id=restaurant.id,
                name="Simulate",
                source_type="file",
                source_url=str(video),
            )
            session.add(camera)
            session.flush()
        if backend == "synthetic":
            # Map the synthetic class names onto same-key categories, if present.
            for class_id, class_name in enumerate(SYNTHETIC_CLASS_NAMES):
                category = session.scalar(
                    select(Category).where(
                        Category.restaurant_id == restaurant.id, Category.key == class_name
                    )
                )
                if category is not None:
                    rules.append(
                        MappingRule(
                            model_class_id=class_id,
                            model_class_name=class_name,
                            category_id=category.id,
                            min_confidence=0.0,
                        )
                    )
        else:
            rules.extend(
                session.scalars(
                    select(ClassMapping).where(ClassMapping.restaurant_id == restaurant.id)
                ).all()
            )
        session.commit()
        restaurant_id, camera_id = restaurant.id, camera.id
    engine.dispose()
    return restaurant_id, camera_id, rules


def cmd_simulate(config: AppConfig, args: argparse.Namespace) -> int:
    """Run the counting pipeline over a video file, unpaced, and print counts."""
    from toskana.events.bus import EventBus
    from toskana.events.writer import EventWriter, make_writer_session_factory
    from toskana.vision.line_crossing import LineSpec
    from toskana.vision.mapping import MappingRule
    from toskana.vision.pipeline import CameraPipeline, PipelineSpec

    video = Path(args.video)
    if not video.is_file():
        print(f"video not found: {video}", file=sys.stderr)
        return 1

    x1, y1, x2, y2 = args.line
    restaurant_id, camera_id = 1, 1
    mapping_rules: tuple[Any, ...] = ()
    snapshots_dir: str | None = None
    writer: EventWriter | None = None
    bus = EventBus()

    if args.db:
        restaurant_id, camera_id, raw_rules = _simulate_db_context(
            args.db, config, video, args.backend
        )
        mapping_rules = tuple(MappingRule.from_row(rule) for rule in raw_rules)
        snapshots_dir = config.snapshots_dir
        writer = EventWriter(make_writer_session_factory(args.db), bus=bus)
        writer.start()

    spec = PipelineSpec(
        restaurant_id=restaurant_id,
        camera_id=camera_id,
        source=str(video),
        restaurant_slug=config.active_restaurant_slug,
        source_type="file",
        backend=args.backend,
        device=config.device,
        paced=False,
        lines=(LineSpec(x1=x1, y1=y1, x2=x2, y2=y2, line_id=None, name="simulate"),),
        mapping_rules=mapping_rules,
        snapshots_dir=snapshots_dir,
    )
    try:
        result = CameraPipeline(spec, bus=bus).run_once()
    finally:
        if writer is not None:
            writer.stop()

    summary: dict[str, Any] = {
        "video": str(video),
        "backend": args.backend,
        "line": [x1, y1, x2, y2],
        "frames": result.frames,
        "counts": result.counts,
        "total_positive": sum(result.counts["positive"].values()),
        "total_negative": sum(result.counts["negative"].values()),
        "total": result.total,
        "gaps": result.gaps,
        "db": args.db,
        "events_written": writer.written_events if writer is not None else None,
    }
    if args.json:
        print(json.dumps(summary, sort_keys=True))
        return 0

    print(f"video:   {video}")
    print(f"backend: {args.backend}  frames: {result.frames}")
    for direction in ("positive", "negative"):
        per_class = result.counts[direction]
        total = sum(per_class.values())
        label = "out (kitchen->customers)" if direction == "positive" else "in (returns)"
        print(f"{direction} / {label}: {total}")
        for class_name in sorted(per_class):
            print(f"  {class_name}: {per_class[class_name]}")
    if writer is not None:
        print(f"persisted {writer.written_events} event(s) to {args.db}")
    return 0


def cmd_eval_counting(config: AppConfig, args: argparse.Namespace) -> int:
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
    run = sub.add_parser("run", help="Start the dashboard server (REST + WS + MJPEG)")
    run.add_argument(
        "--no-pipeline",
        action="store_true",
        help="Serve the API without starting camera pipelines (tests/administration)",
    )
    run.add_argument(
        "--host", default=None, help="Bind address (overrides config host, default 127.0.0.1)"
    )
    run.add_argument(
        "--port", type=int, default=None, help="Bind port (overrides config port, default 8420)"
    )
    simulate = sub.add_parser(
        "simulate", help="Run the counting pipeline on a video file (unpaced) and print counts"
    )
    simulate.add_argument("--video", required=True, help="Path to the video file")
    simulate.add_argument(
        "--backend",
        choices=["synthetic", "yolo"],
        default="synthetic",
        help="Tracking backend (default: synthetic)",
    )
    simulate.add_argument(
        "--line",
        type=_parse_line_arg,
        default=(0.5, 0.0, 0.5, 1.0),
        metavar="X1,Y1,X2,Y2",
        help="Counting line, normalized 0..1 (default: 0.5,0.0,0.5,1.0 — vertical center)",
    )
    simulate.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Persist events (and snapshots) into this SQLite database (default: no persistence)",
    )
    simulate.add_argument("--json", action="store_true", help="Print a JSON summary")
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
    return COMMANDS[args.command](config, args)


if __name__ == "__main__":
    sys.exit(main())
