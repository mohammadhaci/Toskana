"""Stage 2 — FRAMES: sample frames from raw videos, drop near-duplicates.

Frames are sampled every ``--interval-s`` seconds and near-duplicates are
dropped by perceptual-hash (phash) Hamming distance against the frames
already kept for that video. Each video gets its own subdirectory (the
dataset builder later uses this grouping as the train/val leakage guard),
with frame files named ``<video-stem>_f<frame-index>.jpg``.

Usage:
    python training/extract_frames.py --raw data/raw/ --out data/frames/ \
        [--interval-s 1.0] [--phash-threshold 8]

``--phash-threshold``: drop a sampled frame whose phash distance to any kept
frame of the same video is <= threshold; negative disables deduplication.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import list_videos, load_manifest, write_manifest  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sample frames from raw videos with phash near-duplicate dropping."
    )
    parser.add_argument("--raw", required=True, help="raw videos directory (ingest output)")
    parser.add_argument("--out", required=True, help="frames output directory")
    parser.add_argument(
        "--interval-s", type=float, default=1.0, help="sampling interval in seconds (default: 1.0)"
    )
    parser.add_argument(
        "--phash-threshold",
        type=int,
        default=8,
        help="drop frames with phash distance <= threshold to a kept frame; <0 disables",
    )
    parser.add_argument(
        "--jpeg-quality", type=int, default=92, help="JPEG quality for saved frames (default: 92)"
    )
    return parser


def extract_video(
    video: Path, out_dir: Path, interval_s: float, phash_threshold: int, jpeg_quality: int
) -> dict[str, Any]:
    """Extract frames of one video into ``out_dir/<stem>/``; returns stats."""
    import cv2
    import imagehash
    from PIL import Image

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"cannot open video: {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        fps = 15.0  # container did not report FPS; assume a sane default
    step = max(1, round(fps * interval_s))

    video_out = out_dir / video.stem
    video_out.mkdir(parents=True, exist_ok=True)
    kept_hashes: list[Any] = []
    frames_read = sampled = kept = dropped = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            index = frames_read
            frames_read += 1
            if index % step != 0:
                continue
            sampled += 1
            phash = imagehash.phash(Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)))
            if phash_threshold >= 0 and any(
                (phash - old) <= phash_threshold for old in kept_hashes
            ):
                dropped += 1
                continue
            kept_hashes.append(phash)
            path = video_out / f"{video.stem}_f{index:06d}.jpg"
            cv2.imwrite(str(path), frame, [int(cv2.IMWRITE_JPEG_QUALITY), jpeg_quality])
            kept += 1
    finally:
        cap.release()
    return {
        "video": video.name,
        "fps": round(fps, 3),
        "frames_read": frames_read,
        "sampled": sampled,
        "kept": kept,
        "dropped_near_duplicates": dropped,
        "frames_dir": video.stem,
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    raw_dir = Path(args.raw)
    if not raw_dir.is_dir():
        print(f"extract_frames: raw directory not found: {raw_dir}", file=sys.stderr)
        return 1
    videos = list_videos(raw_dir)
    if not videos:
        print(f"extract_frames: no video files in {raw_dir}", file=sys.stderr)
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    stats = [
        extract_video(video, out_dir, args.interval_s, args.phash_threshold, args.jpeg_quality)
        for video in videos
    ]
    for entry in stats:
        print(
            f"{entry['video']}: kept {entry['kept']}/{entry['sampled']} sampled frames "
            f"({entry['dropped_near_duplicates']} near-duplicates dropped)"
        )
    write_manifest(
        out_dir,
        "extract_frames",
        {
            "raw": str(raw_dir),
            "interval_s": args.interval_s,
            "phash_threshold": args.phash_threshold,
            "jpeg_quality": args.jpeg_quality,
        },
        videos=stats,
        source_manifest=load_manifest(raw_dir),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
