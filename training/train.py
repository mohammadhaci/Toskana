"""Stage 6 — TRAIN: ultralytics YOLO training wrapper.

Real training (GPU) and CPU smoke training use the same script; only the
arguments differ. See ``training/README.md`` for GPU setup and expected
runtimes.

Smoke mode (CPU, offline — trains from scratch, random init, no pretrained
weights needed):
    python training/train.py --data data/datasets/NAME/data.yaml \
        --base yolov8n.yaml --epochs 2 --imgsz 160 --out runs/smoke

Real training (GPU):
    python training/train.py --data data/datasets/NAME/data.yaml \
        --base yolov8n.pt --epochs 100 --imgsz 640 --device 0 --out runs/NAME

Prints the resulting ``best.pt`` path and writes a manifest into ``--out``.
``--base *.pt`` needs the weights locally (or network to download them);
failure to load them exits 2 with guidance.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import load_manifest, write_manifest  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Train a YOLO model on a built dataset.")
    parser.add_argument("--data", required=True, help="path to the dataset's data.yaml")
    parser.add_argument(
        "--base",
        default="yolov8n.pt",
        help="base model: pretrained weights (yolov8n.pt) or architecture yaml "
        "(yolov8n.yaml = train from scratch, works offline). Default: yolov8n.pt",
    )
    parser.add_argument("--epochs", type=int, default=100)
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument(
        "--device", default="auto", help="auto | cpu | cuda | mps | GPU index (default: auto)"
    )
    parser.add_argument("--out", required=True, help="run directory, e.g. runs/NAME")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_yaml = Path(args.data)
    if not data_yaml.is_file():
        print(f"train: data.yaml not found: {data_yaml}", file=sys.stderr)
        return 1
    out_dir = Path(args.out)

    from ultralytics import YOLO

    from toskana.vision.backends.yolo import select_device

    device = select_device(args.device)
    try:
        model = YOLO(args.base)
    except Exception as exc:
        print(
            f"train: cannot load base model {args.base!r} ({type(exc).__name__}: {exc}).\n"
            "Pretrained '.pt' weights must exist locally or be downloadable.\n"
            "Options:\n"
            "  * download the weights on a networked machine and pass their path via --base\n"
            "  * train from scratch (offline, random init): --base yolov8n.yaml",
            file=sys.stderr,
        )
        return 2

    print(f"training {args.base} on {data_yaml} (device={device}, epochs={args.epochs})")
    model.train(
        data=str(data_yaml.resolve()),
        epochs=args.epochs,
        imgsz=args.imgsz,
        batch=args.batch,
        device=device,
        project=str(out_dir.parent),
        name=out_dir.name,
        exist_ok=True,
        plots=False,  # avoids font downloads; metrics come from evaluate.py
        workers=2,
        seed=0,
        verbose=False,
    )

    trainer = model.trainer
    best = Path(trainer.best) if trainer is not None else out_dir / "weights" / "best.pt"
    if not best.is_file():
        print(f"train: training finished but best weights missing: {best}", file=sys.stderr)
        return 1
    write_manifest(
        out_dir,
        "train",
        {
            "data": str(data_yaml.resolve()),
            "base": args.base,
            "epochs": args.epochs,
            "imgsz": args.imgsz,
            "batch": args.batch,
            "device": device,
        },
        best=str(best),
        source_manifest=load_manifest(data_yaml.parent),
    )
    print(f"best weights: {best}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
