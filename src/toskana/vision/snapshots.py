"""Per-event annotated snapshots (audit trail: one JPEG per counted crossing).

Snapshots are written only when an event is emitted — never per frame — to
``{base_dir}/{restaurant_slug}/{YYYY-MM-DD}/{event_id}.jpg`` (date in UTC
from the event timestamp). The returned path is *relative* to ``base_dir``
and is what gets stored in ``events.snapshot_path``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import cv2
import numpy as np

from toskana.vision.geometry import Point

_BBOX_COLOR = (80, 220, 80)  # BGR green
_LINE_COLOR = (60, 200, 255)  # BGR amber
_TEXT_COLOR = (255, 255, 255)
_JPEG_QUALITY = 88


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

        annotated = frame.copy()
        (a, b) = line_px
        cv2.line(
            annotated,
            (int(round(a[0])), int(round(a[1]))),
            (int(round(b[0])), int(round(b[1]))),
            _LINE_COLOR,
            2,
        )
        x1, y1, x2, y2 = (int(round(v)) for v in bbox_px)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), _BBOX_COLOR, 2)
        text_y = y1 - 6 if y1 >= 16 else y2 + 16
        cv2.putText(
            annotated,
            label,
            (max(0, x1), text_y),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.5,
            _TEXT_COLOR,
            1,
            cv2.LINE_AA,
        )
        ok = cv2.imwrite(str(out_path), annotated, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        if not ok:
            raise OSError(f"failed to write snapshot: {out_path}")
        return relative.as_posix()
