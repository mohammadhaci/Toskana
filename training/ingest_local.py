"""Stage 1b — INGEST (local files): copy local videos into the raw layout.

This is the permanent offline/CI path (and the **production** path: models
you deploy should be trained on the restaurant's own consented footage).
Videos are copied (or hardlinked with ``--link``) into ``--out`` and recorded
in the same ``manifest.json`` format as the YouTube ingest, with license
``"local"``.

Usage:
    python training/ingest_local.py --videos clip1.mp4 clip2.mp4 --out data/raw/
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):  # standalone: make `training._common` importable
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from training._common import VIDEO_EXTENSIONS, load_manifest, write_manifest  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Copy/link local video files into a raw-data directory + manifest."
    )
    parser.add_argument("--videos", nargs="+", required=True, help="video file path(s)")
    parser.add_argument("--out", required=True, help="output directory, e.g. data/raw/")
    parser.add_argument(
        "--link", action="store_true", help="hardlink instead of copying (same filesystem only)"
    )
    return parser


def _unique_destination(out_dir: Path, name: str) -> Path:
    """Pick a collision-free destination name inside ``out_dir``."""
    dest = out_dir / name
    stem, suffix = dest.stem, dest.suffix
    counter = 1
    while dest.exists():
        dest = out_dir / f"{stem}-{counter}{suffix}"
        counter += 1
    return dest


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict[str, Any]] = []
    for raw in args.videos:
        source = Path(raw).resolve()
        if not source.is_file():
            print(f"ingest_local: not a file: {source}", file=sys.stderr)
            return 1
        if source.suffix.lower() not in VIDEO_EXTENSIONS:
            print(
                f"ingest_local: unsupported extension {source.suffix!r}: {source}", file=sys.stderr
            )
            return 1
        dest = _unique_destination(out_dir, source.name)
        if args.link:
            os.link(source, dest)
        else:
            shutil.copy2(source, dest)
        entries.append({"file": dest.name, "source_path": str(source), "license": "local"})
        print(f"ingested: {dest}")

    existing = load_manifest(out_dir) or {}
    videos = {v["file"]: v for v in existing.get("videos", []) if isinstance(v, dict)}
    for entry in entries:
        videos[entry["file"]] = entry
    write_manifest(
        out_dir,
        "ingest",
        {"link": args.link},
        videos=[videos[key] for key in sorted(videos)],
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
