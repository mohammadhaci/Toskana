"""Stage 4b — VALIDATE: format-check YOLO labels against their images.

Checks every ``.txt`` under ``--labels`` (mirroring the frames layout):
  * exactly 5 whitespace-separated fields per line,
  * class id is an integer within ``[0, num_classes)`` (from ``classes.txt``
    in the labels directory, or ``--classes``),
  * cx/cy/w/h are floats in 0..1 with w,h > 0,
  * every label file has a matching image (orphan labels = error),
  * images without a label file are reported (warning only — they count as
    background images in YOLO training).

Exit codes: 0 = valid, 1 = invalid labels found, 2 = usage error.

Usage:
    python training/validate_labels.py --labels data/labels/ --images data/frames/
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import IMAGE_EXTENSIONS, list_images  # noqa: E402

SPECIAL_FILES = {"classes.txt", "review_queue.txt"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate YOLO-format labels against images.")
    parser.add_argument("--labels", required=True, help="labels directory (autolabel output)")
    parser.add_argument("--images", required=True, help="images/frames directory")
    parser.add_argument(
        "--classes",
        help="comma-separated class names (default: read classes.txt in the labels dir)",
    )
    return parser


def load_classes(labels_dir: Path, requested: str | None) -> list[str] | None:
    if requested:
        return [name.strip() for name in requested.split(",") if name.strip()]
    classes_file = labels_dir / "classes.txt"
    if classes_file.is_file():
        return [line.strip() for line in classes_file.read_text(encoding="utf-8").splitlines()
                if line.strip()]
    return None


def _find_image(images_dir: Path, rel_txt: Path) -> Path | None:
    for ext in sorted(IMAGE_EXTENSIONS):
        candidate = images_dir / rel_txt.with_suffix(ext)
        if candidate.is_file():
            return candidate
    return None


def validate_label_file(path: Path, num_classes: int | None) -> list[str]:
    """Return a list of error strings for one label file (empty = valid)."""
    errors: list[str] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        where = f"{path}:{lineno}"
        parts = line.split()
        if len(parts) != 5:
            errors.append(f"{where}: expected 5 fields, got {len(parts)}")
            continue
        try:
            class_id = int(parts[0])
        except ValueError:
            errors.append(f"{where}: class id is not an integer: {parts[0]!r}")
            continue
        if class_id < 0 or (num_classes is not None and class_id >= num_classes):
            bound = f"0..{num_classes - 1}" if num_classes is not None else ">= 0"
            errors.append(f"{where}: class id {class_id} out of range ({bound})")
        try:
            cx, cy, w, h = (float(v) for v in parts[1:])
        except ValueError:
            errors.append(f"{where}: non-numeric coordinates: {line!r}")
            continue
        for name, value in (("cx", cx), ("cy", cy), ("w", w), ("h", h)):
            if not 0.0 <= value <= 1.0:
                errors.append(f"{where}: {name}={value} outside 0..1")
        if w <= 0 or h <= 0:
            errors.append(f"{where}: non-positive box size w={w} h={h}")
    return errors


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    labels_dir = Path(args.labels)
    images_dir = Path(args.images)
    if not labels_dir.is_dir():
        print(f"validate_labels: labels directory not found: {labels_dir}", file=sys.stderr)
        return 2
    if not images_dir.is_dir():
        print(f"validate_labels: images directory not found: {images_dir}", file=sys.stderr)
        return 2

    classes = load_classes(labels_dir, args.classes)
    num_classes = len(classes) if classes else None
    label_files = sorted(
        p
        for p in labels_dir.rglob("*.txt")
        if p.name not in SPECIAL_FILES and p.is_file()
    )
    if not label_files:
        print(f"validate_labels: no label files under {labels_dir}", file=sys.stderr)
        return 1

    errors: list[str] = []
    labeled_stems: set[str] = set()
    for label in label_files:
        rel = label.relative_to(labels_dir)
        labeled_stems.add(rel.with_suffix("").as_posix())
        errors.extend(validate_label_file(label, num_classes))
        if _find_image(images_dir, rel) is None:
            errors.append(f"{label}: orphan label — no matching image under {images_dir}")

    unlabeled = [
        img.relative_to(images_dir).as_posix()
        for img in list_images(images_dir)
        if img.relative_to(images_dir).with_suffix("").as_posix() not in labeled_stems
    ]
    for rel_img in unlabeled:
        print(f"warning: image without label (treated as background): {rel_img}")

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"validate_labels: INVALID — {len(errors)} error(s)", file=sys.stderr)
        return 1
    print(
        f"validate_labels: OK — {len(label_files)} label files valid"
        + (f", {len(unlabeled)} background image(s)" if unlabeled else "")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
