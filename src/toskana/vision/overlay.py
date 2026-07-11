"""Shared cv2 drawing helpers for event snapshots and live previews.

Extracted from :mod:`toskana.vision.snapshots` so the same annotation style
serves both the per-event audit JPEGs and the live MJPEG/snapshot preview
frames rendered by :class:`~toskana.vision.manager.PipelineManager`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import cv2
import numpy as np

from toskana.vision.detector import TrackedDetection
from toskana.vision.geometry import Point

BBOX_COLOR = (80, 220, 80)  # BGR green
COAST_COLOR = (140, 140, 140)  # BGR grey for coasting tracks
LINE_COLOR = (60, 200, 255)  # BGR amber
TEXT_COLOR = (255, 255, 255)
COUNTS_BG_COLOR = (0, 0, 0)
JPEG_QUALITY = 88


def draw_line(frame: np.ndarray, a: Point, b: Point, *, thickness: int = 2) -> None:
    """Draw one counting line segment (in place)."""
    cv2.line(
        frame,
        (int(round(a[0])), int(round(a[1]))),
        (int(round(b[0])), int(round(b[1]))),
        LINE_COLOR,
        thickness,
    )


def draw_bbox(
    frame: np.ndarray,
    bbox_px: tuple[float, float, float, float],
    label: str,
    *,
    color: tuple[int, int, int] = BBOX_COLOR,
) -> None:
    """Draw one labelled bounding box (in place)."""
    x1, y1, x2, y2 = (int(round(v)) for v in bbox_px)
    cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
    text_y = y1 - 6 if y1 >= 16 else y2 + 16
    cv2.putText(
        frame,
        label,
        (max(0, x1), text_y),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.5,
        TEXT_COLOR,
        1,
        cv2.LINE_AA,
    )


def annotate_event(
    frame: np.ndarray,
    *,
    bbox_px: tuple[float, float, float, float],
    line_px: tuple[Point, Point],
    label: str,
) -> np.ndarray:
    """Render the per-event snapshot annotation (line + bbox + label)."""
    annotated = frame.copy()
    draw_line(annotated, line_px[0], line_px[1])
    draw_bbox(annotated, bbox_px, label)
    return annotated


def render_live(
    frame: np.ndarray,
    tracked: Iterable[TrackedDetection],
    lines_px: Iterable[tuple[Point, Point]],
    counts: Mapping[str, Mapping[str, int]],
) -> np.ndarray:
    """Render a live preview frame: lines + track boxes + running counts.

    ``counts`` uses the pipeline vocabulary ``{"positive": {...}, "negative":
    {...}}`` (positive = out, negative = in / returns).
    """
    annotated = frame.copy()
    for a, b in lines_px:
        draw_line(annotated, a, b)
    for det in tracked:
        color = COAST_COLOR if det.coasting else BBOX_COLOR
        label = f"#{det.track_id} {det.class_name} {det.confidence:.2f}"
        draw_bbox(annotated, det.bbox, label, color=color)
    total_out = sum(counts.get("positive", {}).values())
    total_in = sum(counts.get("negative", {}).values())
    banner = f"out: {total_out}  in: {total_in}  net: {total_out - total_in}"
    (text_w, text_h), baseline = cv2.getTextSize(banner, cv2.FONT_HERSHEY_SIMPLEX, 0.55, 1)
    cv2.rectangle(
        annotated, (4, 4), (12 + text_w, 12 + text_h + baseline), COUNTS_BG_COLOR, thickness=-1
    )
    cv2.putText(
        annotated,
        banner,
        (8, 8 + text_h),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.55,
        TEXT_COLOR,
        1,
        cv2.LINE_AA,
    )
    return annotated


def encode_jpeg(frame: np.ndarray, *, quality: int = JPEG_QUALITY) -> bytes:
    """Encode a BGR frame as JPEG bytes."""
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise ValueError("cv2.imencode failed for JPEG")
    return bytes(buffer.tobytes())
