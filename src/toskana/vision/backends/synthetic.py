"""HSV-threshold detector for the deterministic synthetic scenario videos.

The synthetic video generator (``tests/tools``) draws filled shapes in the
pure colors of :data:`COLOR_CLASS` on a dark background; this backend
recovers them with per-class HSV thresholding and contour bounding boxes.
It implements the same :class:`~toskana.vision.detector.DetectorBackend`
protocol as the YOLO backend, so the entire counting stack can be exercised
in CI without ML dependencies, weights or network.
"""

from __future__ import annotations

import cv2
import numpy as np

from toskana.vision.detector import Detection

#: class_key -> pure BGR fill color used by the synthetic video generator.
COLOR_CLASS: dict[str, tuple[int, int, int]] = {
    "drink": (255, 0, 0),  # pure blue
    "main": (0, 0, 255),  # pure red
    "dessert": (0, 255, 255),  # pure yellow
}

#: Synthetic model classes; ``class_id`` = index in this list.
SYNTHETIC_CLASS_NAMES: list[str] = list(COLOR_CLASS)

_HsvRange = tuple[tuple[int, int, int], tuple[int, int, int]]

# Inclusive (low, high) HSV ranges per class (OpenCV hue scale 0..180).
# Generous S/V floors keep detection robust to lossy video compression;
# the dark background stays far below the V floor -> no false positives.
_HSV_RANGES: dict[str, list[_HsvRange]] = {
    "drink": [((105, 110, 70), (135, 255, 255))],  # blue, hue ~120
    "main": [((0, 110, 70), (10, 255, 255)), ((170, 110, 70), (180, 255, 255))],  # red wraps
    "dessert": [((20, 110, 70), (40, 255, 255))],  # yellow, hue ~30
}


class SyntheticShapeDetector:
    """Detect the generator's colored shapes with pixel-tight boxes."""

    def __init__(self, *, min_area_px: float = 120.0, confidence: float = 0.99) -> None:
        self._min_area_px = min_area_px
        self._confidence = confidence

    @property
    def class_names(self) -> list[str]:
        return list(SYNTHETIC_CLASS_NAMES)

    def detect(self, frame: np.ndarray) -> list[Detection]:
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        detections: list[Detection] = []
        for class_id, class_name in enumerate(SYNTHETIC_CLASS_NAMES):
            mask: np.ndarray | None = None
            for low, high in _HSV_RANGES[class_name]:
                part = cv2.inRange(hsv, np.array(low, np.uint8), np.array(high, np.uint8))
                mask = part if mask is None else cv2.bitwise_or(mask, part)
            assert mask is not None
            contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            for contour in contours:
                x, y, w, h = cv2.boundingRect(contour)
                if w * h < self._min_area_px:
                    continue
                detections.append(
                    Detection(
                        x1=float(x),
                        y1=float(y),
                        x2=float(x + w),
                        y2=float(y + h),
                        class_id=class_id,
                        class_name=class_name,
                        confidence=self._confidence,
                    )
                )
        detections.sort(key=lambda d: (d.class_id, d.x1, d.y1))
        return detections
