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
) -> tuple[int, int, list[Any], list[Any]]:
    """Prepare a DB for persistence: schema + simulate restaurant/camera rows.

    Returns ``(restaurant_id, camera_id, mapping_rules, menu_item_infos)``.
    """
    from sqlalchemy import select

    from toskana.db.base import Base, make_engine, make_session_factory
    from toskana.db.models import Camera, Category, ClassMapping, MenuItem, Restaurant
    from toskana.vision.backends.synthetic import SYNTHETIC_CLASS_NAMES
    from toskana.vision.mapping import MappingRule, MenuItemInfo

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
            # Map the synthetic class names onto menu items (a class_mappings
            # row for the class name that targets a menu item — Phase 2) or,
            # failing that, onto same-key categories, if present.
            item_mappings = {
                row.model_class_name: row
                for row in session.scalars(
                    select(ClassMapping)
                    .where(
                        ClassMapping.restaurant_id == restaurant.id,
                        ClassMapping.menu_item_id.is_not(None),
                        ClassMapping.model_class_name.in_(SYNTHETIC_CLASS_NAMES),
                    )
                    .order_by(ClassMapping.id)
                )
            }
            for class_id, class_name in enumerate(SYNTHETIC_CLASS_NAMES):
                mapping = item_mappings.get(class_name)
                if mapping is not None:
                    rules.append(
                        MappingRule(
                            model_class_id=class_id,
                            model_class_name=class_name,
                            category_id=mapping.category_id,
                            menu_item_id=mapping.menu_item_id,
                            min_confidence=0.0,
                        )
                    )
                    continue
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
        menu_items = [
            MenuItemInfo.from_row(row)
            for row in session.scalars(
                select(MenuItem)
                .where(MenuItem.restaurant_id == restaurant.id)
                .order_by(MenuItem.id)
            )
        ]
        session.commit()
        restaurant_id, camera_id = restaurant.id, camera.id
    engine.dispose()
    return restaurant_id, camera_id, rules, menu_items


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
    menu_items: tuple[Any, ...] = ()
    snapshots_dir: str | None = None
    writer: EventWriter | None = None
    bus = EventBus()

    if args.db:
        restaurant_id, camera_id, raw_rules, raw_items = _simulate_db_context(
            args.db, config, video, args.backend
        )
        mapping_rules = tuple(MappingRule.from_row(rule) for rule in raw_rules)
        menu_items = tuple(raw_items)
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
        menu_items=menu_items,
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


def _line_spec(coords: tuple[float, float, float, float], name: str) -> Any:
    from toskana.vision.line_crossing import LineSpec

    x1, y1, x2, y2 = coords
    return LineSpec(x1=x1, y1=y1, x2=x2, y2=y2, line_id=None, name=name)


def cmd_eval_counting(config: AppConfig, args: argparse.Namespace) -> int:
    """Replay annotated clip(s) through the counting pipeline and score the
    canonical counts against ``ground_truth.json`` (precision/recall/MAE per
    category and per hour of day)."""
    from toskana.eval.runner import evaluate_videos, record_eval_run

    video = Path(args.video)
    gt_path = Path(args.gt)
    for path, label in ((video, "video"), (gt_path, "ground truth")):
        if not path.is_file():
            print(f"{label} not found: {path}", file=sys.stderr)
            return 1
    if args.video2 is not None and not Path(args.video2).is_file():
        print(f"video2 not found: {args.video2}", file=sys.stderr)
        return 1
    ground_truth = json.loads(gt_path.read_text(encoding="utf-8"))

    outcome = evaluate_videos(
        video,
        _line_spec(args.line, "eval"),
        ground_truth,
        backend=args.backend,
        video2=args.video2,
        line2=_line_spec(args.line2, "eval-cam2") if args.line2 is not None else None,
        tolerance_ms=args.tolerance_ms,
        dedup_window_ms=args.dedup_window_ms,
        device=config.device,
    )
    run_config = {
        "backend": args.backend,
        "line": list(args.line),
        "line2": list(args.line2) if args.line2 is not None else None,
        "tolerance_ms": args.tolerance_ms,
        "dedup_window_ms": args.dedup_window_ms,
        "device": config.device,
    }
    run_id: int | None = None
    if args.db:
        video_ref = str(video) if args.video2 is None else f"{video},{args.video2}"
        record = record_eval_run(
            args.db,
            config,
            outcome,
            video_ref=video_ref,
            ground_truth_path=str(gt_path),
            run_config=run_config,
            name=args.name,
        )
        run_id = record.run_id

    if args.json:
        print(
            json.dumps(
                {
                    "scenario": outcome.scenario,
                    "frames": outcome.frames,
                    "raw_measured_total": outcome.raw_measured_total,
                    "run_id": run_id,
                    **outcome.report,
                },
                sort_keys=True,
            )
        )
    else:
        _print_eval_report(outcome, run_id=run_id, db=args.db)
    if args.min_score is not None:
        from toskana.eval.counting import min_score_failures

        failures = min_score_failures(outcome.report, args.min_score, args.min_score_scope)
        if failures:
            print(
                f"FAIL: below --min-score {args.min_score} "
                f"(scope {args.min_score_scope}): {'; '.join(failures)}",
                file=sys.stderr,
            )
            return 1
    return 0


def _print_eval_report(outcome: Any, *, run_id: int | None, db: str | None) -> None:
    report = outcome.report
    overall = report["overall"]
    if outcome.scenario:
        print(f"scenario: {outcome.scenario}")
    frames = "  ".join(f"{cam}={n}" for cam, n in outcome.frames.items())
    print(f"frames:   {frames}")
    print(
        f"crossings: ground truth {overall['gt']}, measured {overall['measured']} "
        f"canonical ({outcome.raw_measured_total} raw)"
    )
    print(
        f"overall:  precision {overall['precision']:.4f}  recall {overall['recall']:.4f}  "
        f"f1 {overall['f1']:.4f}  count MAE {overall['count_mae']:.4f}"
    )
    header = (
        f"{'':12s} {'gt':>4s} {'meas':>4s} {'tp':>4s} {'fp':>4s} {'fn':>4s} "
        f"{'prec':>7s} {'rec':>7s}"
    )
    for title, section in (("per category", "per_category"), ("per hour", "per_hour")):
        print(f"{title}:")
        print(header)
        for key, row in report[section].items():
            print(
                f"  {key:10s} {row['gt']:>4d} {row['measured']:>4d} {row['tp']:>4d} "
                f"{row['fp']:>4d} {row['fn']:>4d} {row['precision']:>7.4f} {row['recall']:>7.4f}"
            )
    if run_id is not None:
        print(f"recorded counting_eval_runs row {run_id} in {db}")


def cmd_cleanup(config: AppConfig, args: argparse.Namespace) -> int:
    """Run the snapshot retention pass once (for cron / manual use)."""
    from toskana.db.base import make_engine, make_session_factory
    from toskana.retention import cleanup_snapshots

    if not Path(config.db_path).exists():
        print(f"Database {config.db_path} does not exist — run `toskana init-db` first")
        return 1
    engine = make_engine(config.db_path)
    try:
        with make_session_factory(engine)() as session:
            result = cleanup_snapshots(config, session)
    finally:
        engine.dispose()
    print(
        f"retention ({config.snapshot_retention_days}d): "
        f"{result.deleted_snapshots} expired snapshot(s) deleted, "
        f"{result.cleared_events} event reference(s) cleared, "
        f"{result.orphans_removed} orphan(s) removed, "
        f"{result.removed_dirs} empty dir(s) pruned"
    )
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
    evaluate = sub.add_parser(
        "eval-counting",
        help="Replay annotated clip(s) and score counts against a ground_truth.json",
    )
    evaluate.add_argument("--video", required=True, help="Path to the annotated video clip")
    evaluate.add_argument(
        "--gt",
        required=True,
        help="Path to ground_truth.json ('crossings' or two-camera 'canonical_crossings')",
    )
    evaluate.add_argument(
        "--line",
        type=_parse_line_arg,
        default=(0.5, 0.0, 0.5, 1.0),
        metavar="X1,Y1,X2,Y2",
        help="Counting line, normalized 0..1 (default: 0.5,0.0,0.5,1.0 — vertical center)",
    )
    evaluate.add_argument(
        "--video2",
        default=None,
        help="Second camera of the same exit; canonical counts after dedup are scored",
    )
    evaluate.add_argument(
        "--line2",
        type=_parse_line_arg,
        default=None,
        metavar="X1,Y1,X2,Y2",
        help="Counting line for --video2 (default: same as --line)",
    )
    evaluate.add_argument(
        "--backend",
        choices=["synthetic", "yolo"],
        default="synthetic",
        help="Tracking backend (default: synthetic)",
    )
    evaluate.add_argument(
        "--tolerance-ms",
        type=int,
        default=2000,
        help="Max |Δt| between a ground-truth and a measured crossing to match (default: 2000)",
    )
    evaluate.add_argument(
        "--dedup-window-ms",
        type=int,
        default=2000,
        help="Cross-camera dedup window for two-camera runs (default: 2000)",
    )
    evaluate.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Record the run into counting_eval_runs in this SQLite database",
    )
    evaluate.add_argument("--name", default=None, help="Name for the recorded eval run")
    evaluate.add_argument(
        "--min-score",
        type=float,
        default=None,
        help="Exit non-zero when precision or recall falls below this (e.g. 0.9)",
    )
    evaluate.add_argument(
        "--min-score-scope",
        choices=["overall", "per-hour", "both"],
        default="both",
        help="Buckets --min-score gates: overall metrics, every per-hour bucket, "
        "or both (default: both)",
    )
    evaluate.add_argument("--json", action="store_true", help="Print the full report as JSON")
    sub.add_parser("cleanup", help="Run the snapshot retention pass once (cron/manual)")
    return parser


COMMANDS = {
    "init-db": cmd_init_db,
    "seed": cmd_seed,
    "run": cmd_run,
    "simulate": cmd_simulate,
    "eval-counting": cmd_eval_counting,
    "cleanup": cmd_cleanup,
}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = load_config(args.config)
    return COMMANDS[args.command](config, args)


if __name__ == "__main__":
    sys.exit(main())
