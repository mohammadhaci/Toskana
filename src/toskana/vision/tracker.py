"""Simple deterministic IoU tracker.

Wraps any :class:`~toskana.vision.detector.DetectorBackend` into a
:class:`~toskana.vision.detector.TrackingBackend`: greedy IoU matching with
a centroid-distance fallback for small/fast objects, plus coasting — lost
tracks keep their last bbox for up to ``max_coast_frames`` frames so short
detection dropouts (occlusion) do not change track identity.

Used with the synthetic backend in CI; production uses ByteTrack inside the
YOLO backend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from toskana.vision.detector import Detection, DetectorBackend, TrackedDetection

Bbox = tuple[float, float, float, float]


def bbox_iou(a: Bbox, b: Bbox) -> float:
    """Intersection-over-union of two ``xyxy`` boxes."""
    ix1, iy1 = max(a[0], b[0]), max(a[1], b[1])
    ix2, iy2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
    if inter <= 0.0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


def _center(bbox: Bbox) -> tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0)


@dataclass
class _Track:
    track_id: int
    bbox: Bbox
    class_id: int
    class_name: str
    confidence: float
    age: int = 1  # frames since creation, including coasted frames
    misses: int = 0  # consecutive frames without a matched detection


class IouTracker:
    """Greedy IoU matcher with centroid fallback and coasting."""

    def __init__(
        self,
        backend: DetectorBackend,
        *,
        iou_threshold: float = 0.3,
        centroid_distance_px: float = 60.0,
        max_coast_frames: int = 15,
    ) -> None:
        self._backend = backend
        self._iou_threshold = iou_threshold
        self._centroid_distance_px = centroid_distance_px
        self._max_coast_frames = max_coast_frames
        self._tracks: list[_Track] = []
        self._next_id = 1

    @property
    def class_names(self) -> list[str]:
        return self._backend.class_names

    def detect_and_track(self, frame: np.ndarray) -> list[TrackedDetection]:
        return self.update(self._backend.detect(frame))

    def update(self, detections: list[Detection]) -> list[TrackedDetection]:
        """Advance one frame with the given detections; returns live tracks."""
        for track in self._tracks:
            track.age += 1

        unmatched_tracks = set(range(len(self._tracks)))
        unmatched_dets = set(range(len(detections)))
        matches: dict[int, int] = {}  # track index -> detection index

        # Stage 1: greedy IoU matching (same class only), best overlap first.
        iou_pairs = [
            (bbox_iou(self._tracks[ti].bbox, detections[di].bbox), ti, di)
            for ti in unmatched_tracks
            for di in unmatched_dets
            if self._tracks[ti].class_id == detections[di].class_id
        ]
        iou_pairs.sort(key=lambda p: (-p[0], p[1], p[2]))
        for iou, ti, di in iou_pairs:
            if iou < self._iou_threshold:
                break
            if ti in unmatched_tracks and di in unmatched_dets:
                matches[ti] = di
                unmatched_tracks.discard(ti)
                unmatched_dets.discard(di)

        # Stage 2: centroid-distance fallback for small movements where the
        # boxes no longer overlap enough (same class only), closest first.
        dist_pairs = [
            (math.dist(_center(self._tracks[ti].bbox), detections[di].center), ti, di)
            for ti in unmatched_tracks
            for di in unmatched_dets
            if self._tracks[ti].class_id == detections[di].class_id
        ]
        dist_pairs.sort(key=lambda p: (p[0], p[1], p[2]))
        for dist, ti, di in dist_pairs:
            if dist > self._centroid_distance_px:
                break
            if ti in unmatched_tracks and di in unmatched_dets:
                matches[ti] = di
                unmatched_tracks.discard(ti)
                unmatched_dets.discard(di)

        # Update matched tracks with fresh detections.
        for ti, di in matches.items():
            track, det = self._tracks[ti], detections[di]
            track.bbox = det.bbox
            track.confidence = det.confidence
            track.class_name = det.class_name
            track.misses = 0

        # Unmatched tracks coast on their last bbox until max_coast_frames.
        for ti in unmatched_tracks:
            self._tracks[ti].misses += 1
        self._tracks = [t for t in self._tracks if t.misses <= self._max_coast_frames]

        # Unmatched detections start new tracks (deterministic id order).
        for di in sorted(unmatched_dets):
            det = detections[di]
            self._tracks.append(
                _Track(
                    track_id=self._next_id,
                    bbox=det.bbox,
                    class_id=det.class_id,
                    class_name=det.class_name,
                    confidence=det.confidence,
                )
            )
            self._next_id += 1

        return [
            TrackedDetection(
                x1=t.bbox[0],
                y1=t.bbox[1],
                x2=t.bbox[2],
                y2=t.bbox[3],
                class_id=t.class_id,
                class_name=t.class_name,
                confidence=t.confidence,
                track_id=t.track_id,
                coasting=t.misses > 0,
                track_age=t.age,
            )
            for t in sorted(self._tracks, key=lambda t: t.track_id)
        ]
