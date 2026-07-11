"""Shared helpers for the training-stage scripts (manifests, YOLO label IO)."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

MANIFEST_NAME = "manifest.json"

#: File extensions recognized as videos by the ingest/extract stages.
VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm", ".m4v", ".mpg", ".mpeg"}

#: File extensions recognized as images by the label/dataset stages.
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp"}

#: License note used when a source license could not be determined.
UNKNOWN_LICENSE = "unknown — verify before production use"


def repo_root() -> Path:
    """Repository root (parent of the ``training/`` directory)."""
    return Path(__file__).resolve().parent.parent


def git_sha() -> str:
    """Current git commit SHA, or ``"unknown"`` outside a git checkout."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_root(),
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return proc.stdout.strip() or "unknown"


def utc_now_iso() -> str:
    return datetime.now(tz=UTC).isoformat(timespec="seconds")


def write_manifest(out_dir: Path, stage: str, params: dict[str, Any], **extra: Any) -> Path:
    """Write ``out_dir/manifest.json`` for a stage; returns the manifest path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "stage": stage,
        "created_at": utc_now_iso(),
        "git_sha": git_sha(),
        "params": params,
        **extra,
    }
    path = out_dir / MANIFEST_NAME
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_manifest(directory: Path) -> dict[str, Any] | None:
    """Load ``directory/manifest.json`` if present (None when missing/invalid)."""
    path = Path(directory) / MANIFEST_NAME
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def list_videos(directory: Path) -> list[Path]:
    """Video files directly inside ``directory``, sorted by name."""
    return sorted(
        p for p in Path(directory).iterdir() if p.suffix.lower() in VIDEO_EXTENSIONS and p.is_file()
    )


def list_images(directory: Path) -> list[Path]:
    """All image files under ``directory`` (recursive), sorted by relative path."""
    root = Path(directory)
    return sorted(
        (p for p in root.rglob("*") if p.suffix.lower() in IMAGE_EXTENSIONS and p.is_file()),
        key=lambda p: str(p.relative_to(root)),
    )


# --------------------------------------------------------------------------
# YOLO label format helpers
# --------------------------------------------------------------------------

YoloBox = tuple[int, float, float, float, float]  # class_id, cx, cy, w, h (normalized)


def write_yolo_labels(path: Path, boxes: list[YoloBox]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{c} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}" for c, cx, cy, w, h in boxes]
    path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")


def read_yolo_labels(path: Path) -> list[YoloBox]:
    """Parse a YOLO label file; raises ``ValueError`` on malformed lines."""
    boxes: list[YoloBox] = []
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) != 5:
            raise ValueError(f"{path}:{lineno}: expected 5 fields, got {len(parts)}")
        try:
            class_id = int(parts[0])
            cx, cy, w, h = (float(v) for v in parts[1:])
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: non-numeric field: {line!r}") from exc
        boxes.append((class_id, cx, cy, w, h))
    return boxes
