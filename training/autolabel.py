"""Stage 3 — AUTOLABEL: pre-label frames with a detector, queue weak ones.

Writes one YOLO-format ``.txt`` per image (mirroring the frames directory
structure), a ``classes.txt`` with the label class order, and a
``review_queue.txt`` listing images whose best detection confidence is below
``--review-conf`` (or that have no detections at all) — those need a manual
pass (see ``training/labeling.md``).

Backends:
  * ``synthetic`` — HSV shape detector (drink/main/dessert), no weights or
    network needed; this is the CI path for the synthetic scenario clips.
  * ``yolo``      — pretrained/finetuned YOLO weights (``--model``); requires
    the weights to exist locally or be downloadable. Unavailable weights ->
    exit 2 with guidance.

Usage:
    python training/autolabel.py --frames data/frames/ --out data/labels/ \
        --backend yolo --model yolov8n.pt --classes cup,bottle,bowl
    python training/autolabel.py --frames data/frames/ --out data/labels/ \
        --backend synthetic
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import (  # noqa: E402
    YoloBox,
    list_images,
    load_manifest,
    write_manifest,
    write_yolo_labels,
)

if TYPE_CHECKING:
    import numpy as np

    from toskana.vision.detector import Detection


class _Detector(Protocol):
    @property
    def class_names(self) -> list[str]: ...

    def detect(self, frame: np.ndarray) -> list[Detection]: ...


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Auto-label frames with a detector (YOLO txt output + review queue)."
    )
    parser.add_argument("--frames", required=True, help="frames directory (extract output)")
    parser.add_argument("--out", required=True, help="labels output directory")
    parser.add_argument(
        "--backend", choices=("yolo", "synthetic"), required=True, help="detector backend"
    )
    parser.add_argument(
        "--model", default="yolov8n.pt", help="yolo backend: weights path or stock name"
    )
    parser.add_argument(
        "--classes",
        help="comma-separated class names to keep (label ids = position in this list); "
        "default: all backend classes",
    )
    parser.add_argument(
        "--conf", type=float, default=0.35, help="minimum detection confidence (default: 0.35)"
    )
    parser.add_argument(
        "--review-conf",
        type=float,
        default=0.5,
        help="images whose best confidence is below this go to review_queue.txt (default: 0.5)",
    )
    return parser


def make_backend(args: argparse.Namespace) -> _Detector:
    """Instantiate the detector; raises SystemExit(2) when YOLO weights are unavailable."""
    if args.backend == "synthetic":
        from toskana.vision.backends.synthetic import SyntheticShapeDetector

        return SyntheticShapeDetector()
    try:
        from toskana.vision.backends.yolo import YoloBackend

        return YoloBackend(args.model, conf=args.conf)
    except Exception as exc:
        print(
            f"autolabel: cannot load YOLO weights {args.model!r} ({type(exc).__name__}: {exc}).\n"
            "Pretrained weights may need to be downloaded — this environment might be offline.\n"
            "Options:\n"
            "  * download the weights on a networked machine and pass --model /path/to/best.pt\n"
            "  * use the CI/offline path: --backend synthetic (synthetic scenario clips only)",
            file=sys.stderr,
        )
        raise SystemExit(2) from exc


def resolve_classes(backend_classes: list[str], requested: str | None) -> list[str]:
    if not requested:
        return list(backend_classes)
    classes = [name.strip() for name in requested.split(",") if name.strip()]
    unknown = [name for name in classes if name not in backend_classes]
    if unknown:
        raise SystemExit(
            f"autolabel: classes not provided by the backend: {unknown} "
            f"(backend classes: {backend_classes})"
        )
    return classes


def detections_to_boxes(
    detections: list[Any], classes: list[str], conf: float, width: int, height: int
) -> tuple[list[YoloBox], float]:
    """Convert kept detections to normalized YOLO boxes; returns (boxes, max_conf)."""
    boxes: list[YoloBox] = []
    max_conf = 0.0
    for det in detections:
        max_conf = max(max_conf, det.confidence)
        if det.class_name not in classes or det.confidence < conf:
            continue
        x1, y1 = max(0.0, det.x1), max(0.0, det.y1)
        x2, y2 = min(float(width), det.x2), min(float(height), det.y2)
        if x2 <= x1 or y2 <= y1:
            continue
        boxes.append(
            (
                classes.index(det.class_name),
                (x1 + x2) / 2 / width,
                (y1 + y2) / 2 / height,
                (x2 - x1) / width,
                (y2 - y1) / height,
            )
        )
    return boxes, max_conf


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    frames_dir = Path(args.frames)
    images = list_images(frames_dir) if frames_dir.is_dir() else []
    if not images:
        print(f"autolabel: no images under {frames_dir}", file=sys.stderr)
        return 1

    import cv2

    backend = make_backend(args)
    classes = resolve_classes(backend.class_names, args.classes)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    review: list[str] = []
    class_counts = dict.fromkeys(classes, 0)
    for image in images:
        frame = cv2.imread(str(image))
        if frame is None:
            print(f"autolabel: unreadable image skipped: {image}", file=sys.stderr)
            continue
        rel = image.relative_to(frames_dir)
        boxes, max_conf = detections_to_boxes(
            backend.detect(frame), classes, args.conf, frame.shape[1], frame.shape[0]
        )
        write_yolo_labels(out_dir / rel.with_suffix(".txt"), boxes)
        for class_id, *_ in boxes:
            class_counts[classes[class_id]] += 1
        if not boxes or max_conf < args.review_conf:
            review.append(rel.as_posix())

    (out_dir / "classes.txt").write_text("\n".join(classes) + "\n", encoding="utf-8")
    (out_dir / "review_queue.txt").write_text(
        "\n".join(review) + ("\n" if review else ""), encoding="utf-8"
    )
    write_manifest(
        out_dir,
        "autolabel",
        {
            "frames": str(frames_dir),
            "backend": args.backend,
            "model": args.model if args.backend == "yolo" else None,
            "classes": classes,
            "conf": args.conf,
            "review_conf": args.review_conf,
        },
        images_labeled=len(images),
        review_queue_size=len(review),
        instances_per_class=class_counts,
        source_manifest=load_manifest(frames_dir),
    )
    print(
        f"labeled {len(images)} images "
        f"({sum(class_counts.values())} boxes, {len(review)} queued for review)"
    )
    for name, count in class_counts.items():
        print(f"  {name}: {count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
