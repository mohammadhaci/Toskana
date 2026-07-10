"""Stage 7 — EVALUATE: validate trained weights, write metrics.json, gate.

Runs ultralytics validation on the dataset's val split and writes
``metrics.json`` (mAP50, mAP50-95, per-class AP, provenance fields). This is
the **export gate**: if mAP50 is below ``--min-map50`` the script exits 1 and
the model must not be exported/activated.

Usage:
    python training/evaluate.py --weights runs/NAME/weights/best.pt \
        --data data/datasets/NAME/data.yaml [--min-map50 0.6] [--out metrics.json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import git_sha, utc_now_iso  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate trained weights (mAP) and gate on --min-map50."
    )
    parser.add_argument("--weights", required=True, help="trained weights (best.pt)")
    parser.add_argument("--data", required=True, help="dataset data.yaml")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument(
        "--device", default="auto", help="auto | cpu | cuda | mps (default: auto)"
    )
    parser.add_argument(
        "--min-map50",
        type=float,
        default=0.0,
        help="export gate: exit 1 if mAP50 is below this (default: 0.0 = no gate)",
    )
    parser.add_argument(
        "--out", help="metrics JSON output path (default: metrics.json next to the weights)"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    weights = Path(args.weights)
    if not weights.is_file():
        print(f"evaluate: weights not found: {weights}", file=sys.stderr)
        return 2
    out_path = Path(args.out) if args.out else weights.parent / "metrics.json"

    from ultralytics import YOLO

    from toskana.vision.backends.yolo import select_device

    model = YOLO(str(weights))
    results = model.val(
        data=str(Path(args.data).resolve()),
        imgsz=args.imgsz,
        device=select_device(args.device),
        plots=False,
        verbose=False,
    )
    names = results.names  # dict: class id -> name
    maps = results.box.maps  # per-class mAP50-95, one entry per class id
    per_class = {
        str(names.get(class_id, class_id)): {"map50_95": round(float(value), 5)}
        for class_id, value in enumerate(maps)
    }
    metrics = {
        "map50": round(float(results.box.map50), 5),
        "map50_95": round(float(results.box.map), 5),
        "per_class": per_class,
        "weights": str(weights.resolve()),
        "data": str(Path(args.data).resolve()),
        "imgsz": args.imgsz,
        "min_map50": args.min_map50,
        "created_at": utc_now_iso(),
        "git_sha": git_sha(),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"metrics: {out_path}")
    print(f"  mAP50     = {metrics['map50']}")
    print(f"  mAP50-95  = {metrics['map50_95']}")
    for name, values in per_class.items():
        print(f"  {name}: mAP50-95 = {values['map50_95']}")

    if metrics["map50"] < args.min_map50:
        print(
            f"evaluate: GATE FAILED — mAP50 {metrics['map50']} < required {args.min_map50}; "
            "do not export/activate this model.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
