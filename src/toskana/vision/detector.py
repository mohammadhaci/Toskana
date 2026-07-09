"""Detection and tracking abstractions.

The counting pipeline is written against two small protocols so the whole
counting/tracking/dedup logic runs deterministically in CI with the
:class:`~toskana.vision.backends.synthetic.SyntheticShapeDetector` and an
IoU tracker, while production uses the YOLO backend — both behind the same
interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import numpy as np


@dataclass(frozen=True)
class Detection:
    """A single detection in one frame.

    Bounding box is in absolute pixel coordinates, ``xyxy`` convention
    (``x1, y1`` top-left, ``x2, y2`` bottom-right, exclusive).
    """

    x1: float
    y1: float
    x2: float
    y2: float
    class_id: int
    class_name: str
    confidence: float

    @property
    def bbox(self) -> tuple[float, float, float, float]:
        return (self.x1, self.y1, self.x2, self.y2)

    @property
    def width(self) -> float:
        return self.x2 - self.x1

    @property
    def height(self) -> float:
        return self.y2 - self.y1

    @property
    def area(self) -> float:
        return max(0.0, self.width) * max(0.0, self.height)

    @property
    def center(self) -> tuple[float, float]:
        return ((self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0)

    @property
    def anchor(self) -> tuple[float, float]:
        """Bottom-center anchor point used by the line-crossing engine."""
        return ((self.x1 + self.x2) / 2.0, self.y2)


@dataclass(frozen=True)
class TrackedDetection(Detection):
    """A detection with a stable track identity.

    ``coasting`` is True when the underlying detection vanished this frame
    and the tracker is keeping the track alive with its last known bbox.
    ``track_age`` counts frames since the track was created (including
    coasted frames) — used later for ``min_track_age`` gating.
    """

    track_id: int = -1
    coasting: bool = False
    track_age: int = 1


@runtime_checkable
class DetectorBackend(Protocol):
    """Per-frame object detector."""

    @property
    def class_names(self) -> list[str]:
        """Raw model class names, indexed by ``class_id``."""
        ...

    def detect(self, frame: np.ndarray) -> list[Detection]:
        """Detect objects in a single BGR frame."""
        ...


@runtime_checkable
class TrackingBackend(Protocol):
    """Detector + multi-object tracker with stable track ids."""

    @property
    def class_names(self) -> list[str]:
        """Raw model class names, indexed by ``class_id``."""
        ...

    def detect_and_track(self, frame: np.ndarray) -> list[TrackedDetection]:
        """Detect objects in a single BGR frame and assign track ids."""
        ...
