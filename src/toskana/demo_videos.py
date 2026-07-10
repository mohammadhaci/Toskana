"""Self-contained demo clip generator for the seeded demo restaurant.

``toskana seed`` points the two demo cameras at ``./data/videos/pass_left.mp4``
and ``pass_right.mp4``. This module renders those clips so a fresh install
counts something out of the box (with the default ``synthetic`` detector
backend). The right camera shows the same physical events as the left one,
mirrored and slightly delayed — two viewpoints of one exit, which also
exercises the exit-group dedup.

Deliberately independent from ``tests/tools`` so it ships with the package.
"""

from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

logger = logging.getLogger(__name__)

WIDTH, HEIGHT, FPS = 640, 360, 15
BACKGROUND = (24, 24, 24)
# BGR colors understood by SyntheticShapeDetector's COLOR_CLASS map.
CLASS_COLORS = {
    "drink": (255, 64, 0),  # blue-ish
    "main": (0, 0, 255),  # red
    "dessert": (0, 220, 255),  # yellow
}

# One service loop: (class_key, shape, size, y, start_frame). Items travel
# left -> right across the full width in ~4 s; a 3-item tray crosses together.
_SCHEDULE: tuple[tuple[str, str, int, int, int], ...] = (
    ("drink", "circle", 36, 120, 0),
    ("main", "rect", 44, 200, 45),
    ("drink", "circle", 36, 110, 100),  # tray of three (same start frame)
    ("main", "rect", 44, 180, 100),
    ("main", "rect", 44, 250, 100),
    ("dessert", "circle", 32, 160, 160),
    ("main", "rect", 44, 220, 215),
    ("drink", "circle", 36, 140, 270),
)
_TRAVEL_FRAMES = 60  # 4 s per crossing
_LOOP_FRAMES = 340  # ~23 s per loop
_RIGHT_CAM_DELAY = 3  # ~200 ms viewpoint latency for the second camera


def _render(path: Path, *, mirrored: bool, delay_frames: int) -> None:
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter.fourcc(*"mp4v"), float(FPS), (WIDTH, HEIGHT)
    )
    if not writer.isOpened():
        raise RuntimeError(f"OpenCV cannot open a video writer for {path}")
    try:
        for frame_index in range(_LOOP_FRAMES):
            frame = np.full((HEIGHT, WIDTH, 3), BACKGROUND, np.uint8)
            for class_key, shape, size, y, start in _SCHEDULE:
                progress = (frame_index - start - delay_frames) / _TRAVEL_FRAMES
                if not 0.0 <= progress <= 1.0:
                    continue
                x = 30 + progress * (WIDTH - 60)
                if mirrored:
                    x = WIDTH - x
                cx, cy = int(round(x)), y
                color = CLASS_COLORS[class_key]
                half = size // 2
                if shape == "circle":
                    cv2.circle(frame, (cx, cy), half, color, -1)
                else:
                    cv2.rectangle(frame, (cx - half, cy - half), (cx + half, cy + half), color, -1)
            writer.write(frame)
    finally:
        writer.release()


def ensure_demo_videos(paths: list[str], base_dir: str | Path = ".") -> list[Path]:
    """Render any missing demo clips among ``paths`` (relative to ``base_dir``).

    Returns the list of files that were created. Existing files are left
    untouched. The first path is rendered as the left camera, every further
    path as the mirrored, slightly delayed right camera.
    """
    created: list[Path] = []
    for index, raw in enumerate(paths):
        path = Path(base_dir) / raw
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        _render(path, mirrored=index > 0, delay_frames=_RIGHT_CAM_DELAY if index > 0 else 0)
        logger.info("generated demo video %s", path)
        created.append(path)
    return created
