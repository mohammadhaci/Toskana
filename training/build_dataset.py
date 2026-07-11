"""Stage 5 — DATASET: assemble a YOLO dataset (train/val) from frames+labels.

Produces the standard ultralytics layout under ``--out``::

    images/train  images/val  labels/train  labels/val  data.yaml  manifest.json

**Leakage guard:** frames are grouped by their source video (= subdirectory
under ``--frames``); all frames of one video always land in the same split,
so near-identical neighboring frames can never straddle train/val. The split
is a deterministic greedy assignment (smallest videos to val first) that
approaches ``--val-ratio``. With a single source video everything goes to
train and ``data.yaml`` points val at the train set (a loud warning is
printed — record at least two clips for a real dataset).

Label class ids are remapped from the labels' ``classes.txt`` order to the
``--classes`` order; boxes of classes not in ``--classes`` are dropped (and
counted in the manifest).

Usage:
    python training/build_dataset.py --frames data/frames/ --labels data/labels/ \
        --out data/datasets/NAME/ --classes drink,main,dessert [--val-ratio 0.2]
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import (  # noqa: E402
    YoloBox,
    list_images,
    load_manifest,
    read_yolo_labels,
    write_manifest,
    write_yolo_labels,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build a YOLO dataset (train/val split with per-video leakage guard)."
    )
    parser.add_argument("--frames", required=True, help="frames directory (extract output)")
    parser.add_argument("--labels", required=True, help="labels directory (autolabel/manual)")
    parser.add_argument("--out", required=True, help="dataset output directory")
    parser.add_argument(
        "--classes", required=True, help="comma-separated target class names, e.g. drink,main"
    )
    parser.add_argument(
        "--val-ratio", type=float, default=0.2, help="validation fraction (default: 0.2)"
    )
    return parser


def group_frames_by_video(frames_dir: Path) -> dict[str, list[Path]]:
    """Group images by source video: first path component under the frames dir."""
    groups: dict[str, list[Path]] = {}
    for image in list_images(frames_dir):
        rel = image.relative_to(frames_dir)
        video = rel.parts[0] if len(rel.parts) > 1 else "_root"
        groups.setdefault(video, []).append(image)
    return groups


def assign_splits(groups: dict[str, list[Path]], val_ratio: float) -> dict[str, str]:
    """Whole-video train/val assignment (deterministic, smallest videos to val)."""
    if len(groups) == 1:
        return {video: "train" for video in groups}
    total = sum(len(images) for images in groups.values())
    target_val = total * val_ratio
    splits: dict[str, str] = {}
    val_count = 0
    # Smallest first so val overshoots the target as little as possible;
    # the largest video is always assigned last and therefore stays in train.
    for video in sorted(groups, key=lambda v: (len(groups[v]), v)):
        if val_count < target_val and len(splits) < len(groups) - 1:
            splits[video] = "val"
            val_count += len(groups[video])
        else:
            splits[video] = "train"
    if "val" not in splits.values():  # val_ratio ~0: still keep one video in val
        smallest = min(groups, key=lambda v: (len(groups[v]), v))
        splits[smallest] = "val"
    return splits


def remap_boxes(
    boxes: list[YoloBox], source_classes: list[str], target_classes: list[str]
) -> tuple[list[YoloBox], int]:
    """Remap class ids from source order to target order; returns (boxes, dropped)."""
    remapped: list[YoloBox] = []
    dropped = 0
    for class_id, cx, cy, w, h in boxes:
        if class_id >= len(source_classes):
            raise ValueError(f"class id {class_id} outside source classes {source_classes}")
        name = source_classes[class_id]
        if name not in target_classes:
            dropped += 1
            continue
        remapped.append((target_classes.index(name), cx, cy, w, h))
    return remapped, dropped


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    frames_dir, labels_dir, out_dir = Path(args.frames), Path(args.labels), Path(args.out)
    target_classes = [name.strip() for name in args.classes.split(",") if name.strip()]
    if not target_classes:
        print("build_dataset: --classes must name at least one class", file=sys.stderr)
        return 2

    groups = group_frames_by_video(frames_dir)
    if not groups:
        print(f"build_dataset: no images under {frames_dir}", file=sys.stderr)
        return 1
    classes_file = labels_dir / "classes.txt"
    source_classes = (
        [
            line.strip()
            for line in classes_file.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        if classes_file.is_file()
        else list(target_classes)
    )

    splits = assign_splits(groups, args.val_ratio)
    single_video = len(groups) == 1
    for split in ("train", "val"):
        (out_dir / "images" / split).mkdir(parents=True, exist_ok=True)
        (out_dir / "labels" / split).mkdir(parents=True, exist_ok=True)

    class_counts: dict[str, dict[str, int]] = {
        split: dict.fromkeys(target_classes, 0) for split in ("train", "val")
    }
    image_counts = {"train": 0, "val": 0}
    dropped_total = skipped_unlabeled = 0
    for video, images in sorted(groups.items()):
        split = splits[video]
        for image in images:
            rel = image.relative_to(frames_dir)
            label_path = labels_dir / rel.with_suffix(".txt")
            if not label_path.is_file():
                skipped_unlabeled += 1
                continue
            boxes, dropped = remap_boxes(
                read_yolo_labels(label_path), source_classes, target_classes
            )
            dropped_total += dropped
            shutil.copy2(image, out_dir / "images" / split / image.name)
            write_yolo_labels(out_dir / "labels" / split / f"{image.stem}.txt", boxes)
            image_counts[split] += 1
            for class_id, *_ in boxes:
                class_counts[split][target_classes[class_id]] += 1

    data_yaml: dict[str, Any] = {
        "path": str(out_dir.resolve()),
        "train": "images/train",
        "val": "images/train" if single_video else "images/val",
        "nc": len(target_classes),
        "names": list(target_classes),
    }
    (out_dir / "data.yaml").write_text(yaml.safe_dump(data_yaml, sort_keys=False), "utf-8")

    if single_video:
        print(
            "WARNING: only one source video — no held-out val split is possible "
            "(val = train in data.yaml). Use >= 2 clips for a meaningful dataset.",
            file=sys.stderr,
        )
    print(f"dataset: {out_dir} (classes: {', '.join(target_classes)})")
    for split in ("train", "val"):
        counts = ", ".join(f"{name}={count}" for name, count in class_counts[split].items())
        print(f"  {split}: {image_counts[split]} images | {counts}")
    if dropped_total:
        print(f"  dropped {dropped_total} box(es) of classes outside --classes")
    if skipped_unlabeled:
        print(f"  skipped {skipped_unlabeled} image(s) without a label file")

    write_manifest(
        out_dir,
        "build_dataset",
        {
            "frames": str(frames_dir),
            "labels": str(labels_dir),
            "classes": target_classes,
            "val_ratio": args.val_ratio,
        },
        video_splits=splits,
        image_counts=image_counts,
        class_counts=class_counts,
        dropped_out_of_scope_boxes=dropped_total,
        skipped_unlabeled_images=skipped_unlabeled,
        source_manifests={
            "frames": load_manifest(frames_dir),
            "labels": load_manifest(labels_dir),
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
