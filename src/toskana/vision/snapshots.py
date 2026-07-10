"""Per-event annotated snapshots (audit trail: one JPEG per counted crossing).

Snapshots are written only when an event is emitted — never per frame — to
``{base_dir}/{restaurant_slug}/{YYYY-MM-DD}/{event_id}.jpg`` (date in UTC
from the event timestamp). The returned path is *relative* to ``base_dir``
and is what gets stored in ``events.snapshot_path``.

Drawing primitives live in :mod:`toskana.vision.overlay` (shared with the
live MJPEG preview).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from toskana.vision.geometry import Point
from toskana.vision.overlay import JPEG_QUALITY, annotate_event


class SnapshotSaver:
    """Renders and saves one annotated JPEG per crossing event."""

    def __init__(self, base_dir: str | Path, restaurant_slug: str) -> None:
        self.base_dir = Path(base_dir)
        self.restaurant_slug = restaurant_slug

    def save(
        self,
        frame: np.ndarray,
        *,
        event_id: str,
        ts_ms: int,
        bbox_px: tuple[float, float, float, float],
        line_px: tuple[Point, Point],
        label: str,
    ) -> str:
        """Annotate ``frame`` (bbox + line + label) and write the JPEG.

        Returns the snapshot path relative to ``base_dir`` (POSIX style).
        """
        day = datetime.fromtimestamp(ts_ms / 1000.0, tz=UTC).strftime("%Y-%m-%d")
        relative = Path(self.restaurant_slug) / day / f"{event_id}.jpg"
        out_path = self.base_dir / relative
        out_path.parent.mkdir(parents=True, exist_ok=True)

        annotated = annotate_event(frame, bbox_px=bbox_px, line_px=line_px, label=label)
        ok = cv2.imwrite(str(out_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
        if not ok:
            raise OSError(f"failed to write snapshot: {out_path}")
        return relative.as_posix()
