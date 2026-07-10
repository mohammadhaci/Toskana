"""Stage 8 — EXPORT: publish trained weights into the model registry.

Copies the weights to ``models/{restaurant}/{name}-{version}/best.pt``, writes
``meta.json`` (classes, metrics, provenance/license manifest chain) next to
them, and registers the model in the database:

  * one ``models_registry`` row (kind ``finetuned``, ``is_active`` false —
    activation happens in the dashboard's Models page or via the API), and
  * skeleton ``class_mappings`` rows for every model class whose target
    category is known: either given via ``--map-category CLASS=CATEGORY_KEY``
    or auto-resolved when the class name equals a category key. Classes
    without a resolvable target are listed for the admin to map in the
    dashboard (the DB requires a category or menu item per mapping row, so
    no row can be created for them yet).

Idempotent per (restaurant, name, version): re-running updates the same
registry row and never duplicates mappings.

Usage:
    python training/export_model.py --weights runs/NAME/weights/best.pt \
        --restaurant toskana --name toskana-phase1 --version 1 \
        --db ./toskana.db --metrics metrics.json --map-category drink=drink
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import git_sha, load_manifest, utc_now_iso  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy trained weights into models/ and register them in the DB."
    )
    parser.add_argument("--weights", required=True, help="trained weights (best.pt)")
    parser.add_argument("--restaurant", required=True, help="restaurant slug, e.g. toskana")
    parser.add_argument("--name", required=True, help="model name, e.g. toskana-phase1")
    parser.add_argument("--version", required=True, help="model version, e.g. 1")
    parser.add_argument("--db", required=True, help="path to the SQLite database")
    parser.add_argument("--metrics", help="metrics.json from evaluate.py")
    parser.add_argument(
        "--map-category",
        action="append",
        default=[],
        metavar="CLASS=CATEGORY_KEY",
        help="map a model class to a category key (repeatable); classes whose name "
        "equals a category key are mapped automatically",
    )
    parser.add_argument(
        "--classes",
        help="comma-separated class names in model id order; default: read from the weights",
    )
    parser.add_argument(
        "--dataset", help="dataset directory (its manifest chain is embedded in meta.json)"
    )
    parser.add_argument(
        "--models-dir", default="models", help="registry root directory (default: models/)"
    )
    parser.add_argument(
        "--min-confidence", type=float, default=0.35, help="min_confidence for new mappings"
    )
    return parser


def resolve_classes(args: argparse.Namespace) -> list[str]:
    """Class names in model id order — from --classes or loaded from the weights."""
    if args.classes:
        return [name.strip() for name in args.classes.split(",") if name.strip()]
    try:
        from ultralytics import YOLO

        names = YOLO(args.weights).names
    except Exception as exc:
        print(
            f"export_model: cannot read class names from {args.weights!r} "
            f"({type(exc).__name__}: {exc}). Pass them explicitly, e.g. "
            "--classes drink,main,dessert",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc
    if isinstance(names, dict):
        return [str(names[key]) for key in sorted(names)]
    return [str(name) for name in names]


def parse_map_category(pairs: list[str], classes: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise SystemExit(f"export_model: bad --map-category {pair!r} (want CLASS=KEY)")
        class_name, _, category_key = pair.partition("=")
        if class_name not in classes:
            raise SystemExit(
                f"export_model: --map-category names unknown class {class_name!r} "
                f"(model classes: {classes})"
            )
        mapping[class_name] = category_key
    return mapping


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    weights = Path(args.weights)
    if not weights.is_file():
        print(f"export_model: weights not found: {weights}", file=sys.stderr)
        return 2
    db_path = Path(args.db)
    if not db_path.is_file():
        print(
            f"export_model: database not found: {db_path} (run `toskana init-db` first)",
            file=sys.stderr,
        )
        return 2

    classes = resolve_classes(args)
    explicit_map = parse_map_category(args.map_category, classes)
    metrics: dict[str, Any] | None = None
    if args.metrics:
        metrics = json.loads(Path(args.metrics).read_text(encoding="utf-8"))

    # ---- copy weights + meta.json into the models registry directory ------
    dest_dir = Path(args.models_dir) / args.restaurant / f"{args.name}-{args.version}"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_weights = dest_dir / "best.pt"
    shutil.copy2(weights, dest_weights)
    dataset_manifest = load_manifest(Path(args.dataset)) if args.dataset else None
    meta = {
        "name": args.name,
        "version": args.version,
        "restaurant": args.restaurant,
        "kind": "finetuned",
        "classes": classes,
        "metrics": metrics,
        "weights_source": str(weights.resolve()),
        "dataset_manifest": dataset_manifest,  # embeds frames/ingest manifests (license chain)
        "created_at": utc_now_iso(),
        "git_sha": git_sha(),
    }
    (dest_dir / "meta.json").write_text(
        json.dumps(meta, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # ---- registry row + skeleton class mappings ---------------------------
    from sqlalchemy import select

    from toskana.db.base import make_engine, make_session_factory
    from toskana.db.models import Category, ClassMapping, ModelRegistry, Restaurant

    engine = make_engine(db_path)
    try:
        with make_session_factory(engine)() as session:
            restaurant = session.scalar(
                select(Restaurant).where(Restaurant.slug == args.restaurant)
            )
            if restaurant is None:
                print(
                    f"export_model: no restaurant with slug {args.restaurant!r} in {db_path}",
                    file=sys.stderr,
                )
                return 2

            model = session.scalar(
                select(ModelRegistry).where(
                    ModelRegistry.restaurant_id == restaurant.id,
                    ModelRegistry.name == args.name,
                    ModelRegistry.version == args.version,
                )
            )
            if model is None:
                model = ModelRegistry(
                    restaurant_id=restaurant.id,
                    name=args.name,
                    version=args.version,
                    kind="finetuned",
                    is_active=False,
                    path=str(dest_weights),
                    classes_json=json.dumps(classes),
                )
                session.add(model)
            model.path = str(dest_weights)
            model.classes_json = json.dumps(classes)
            model.metrics_json = json.dumps(metrics) if metrics is not None else None
            session.flush()

            categories = {
                cat.key: cat
                for cat in session.scalars(
                    select(Category).where(Category.restaurant_id == restaurant.id)
                )
            }
            unmapped: list[str] = []
            for class_id, class_name in enumerate(classes):
                category_key = explicit_map.get(class_name)
                if category_key is None and class_name in categories:
                    category_key = class_name  # auto: class name == category key
                if category_key is None:
                    unmapped.append(class_name)
                    continue
                if category_key not in categories:
                    print(
                        f"export_model: unknown category key {category_key!r} for class "
                        f"{class_name!r} (available: {sorted(categories)})",
                        file=sys.stderr,
                    )
                    return 2
                existing = session.scalar(
                    select(ClassMapping).where(
                        ClassMapping.model_id == model.id,
                        ClassMapping.model_class_id == class_id,
                        ClassMapping.restaurant_id == restaurant.id,
                    )
                )
                if existing is None:
                    session.add(
                        ClassMapping(
                            restaurant_id=restaurant.id,
                            model_id=model.id,
                            model_class_id=class_id,
                            model_class_name=class_name,
                            category_id=categories[category_key].id,
                            min_confidence=args.min_confidence,
                        )
                    )
            session.commit()
            model_id = model.id
    finally:
        engine.dispose()

    print(f"exported: {dest_weights}")
    print(f"registry: models_registry id={model_id} ({args.name}-{args.version}, inactive)")
    if unmapped:
        print(
            "note: no category mapping for class(es) "
            f"{', '.join(unmapped)} — complete them in the dashboard (Admin -> Mappings) "
            "before activating the model."
        )
    print("activate via the dashboard Models page (or POST .../models/{id}/activate).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
