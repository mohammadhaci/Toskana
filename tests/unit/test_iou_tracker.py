from __future__ import annotations

import numpy as np

from toskana.vision.detector import Detection, DetectorBackend, TrackingBackend
from toskana.vision.tracker import IouTracker, bbox_iou

FRAME = np.zeros((8, 8, 3), np.uint8)


def det(x: float, y: float, size: float = 30.0, class_id: int = 0) -> Detection:
    names = ["drink", "main", "dessert"]
    return Detection(
        x1=x,
        y1=y,
        x2=x + size,
        y2=y + size,
        class_id=class_id,
        class_name=names[class_id],
        confidence=0.9,
    )


class ScriptedBackend:
    """DetectorBackend replaying a scripted list of per-frame detections."""

    def __init__(self, frames: list[list[Detection]]) -> None:
        self._frames = frames
        self._index = 0

    @property
    def class_names(self) -> list[str]:
        return ["drink", "main", "dessert"]

    def detect(self, frame: np.ndarray) -> list[Detection]:
        detections = self._frames[self._index]
        self._index += 1
        return detections


def test_bbox_iou() -> None:
    assert bbox_iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert bbox_iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0
    assert bbox_iou((0, 0, 10, 10), (5, 0, 15, 10)) == (50 / 150)


def test_satisfies_protocols() -> None:
    backend = ScriptedBackend([])
    tracker = IouTracker(backend)
    assert isinstance(backend, DetectorBackend)
    assert isinstance(tracker, TrackingBackend)
    assert tracker.class_names == backend.class_names


def test_stable_ids_for_two_moving_objects() -> None:
    frames = [[det(10 + 5 * i, 50), det(10 + 5 * i, 200, class_id=1)] for i in range(10)]
    tracker = IouTracker(ScriptedBackend(frames))
    per_object_ids: dict[int, set[int]] = {50: set(), 200: set()}
    for _ in range(10):
        tracked = tracker.detect_and_track(FRAME)
        assert len(tracked) == 2
        for t in tracked:
            per_object_ids[int(t.y1)].add(t.track_id)
    assert len(per_object_ids[50]) == 1
    assert len(per_object_ids[200]) == 1
    assert per_object_ids[50] != per_object_ids[200]


def test_coasting_preserves_id_through_dropout() -> None:
    frames: list[list[Detection]] = []
    for i in range(12):
        frames.append([] if 5 <= i <= 7 else [det(10 + 6 * i, 50)])
    tracker = IouTracker(ScriptedBackend(frames), max_coast_frames=15)
    ids: set[int] = set()
    for i in range(12):
        tracked = tracker.detect_and_track(FRAME)
        assert len(tracked) == 1
        (t,) = tracked
        ids.add(t.track_id)
        assert t.coasting == (5 <= i <= 7)
        if i >= 1:
            assert t.track_age == i + 1  # age keeps counting while coasting
    assert len(ids) == 1


def test_track_dropped_after_max_coast_frames() -> None:
    frames: list[list[Detection]] = [[det(10, 50)], [det(16, 50)]]
    frames += [[] for _ in range(4)]  # gap longer than max_coast_frames
    frames += [[det(46, 50)]]
    tracker = IouTracker(ScriptedBackend(frames), max_coast_frames=2)
    outputs = [tracker.detect_and_track(FRAME) for _ in range(len(frames))]
    assert outputs[2][0].coasting and outputs[2][0].track_id == 1
    assert outputs[5] == []  # track dropped
    assert outputs[6][0].track_id == 2  # reappearance gets a fresh id


def test_centroid_fallback_matches_fast_small_movement() -> None:
    # 20 px boxes jumping 25 px per frame: zero IoU, small centroid distance.
    frames = [[det(10 + 25 * i, 50, size=20)] for i in range(6)]
    tracker = IouTracker(ScriptedBackend(frames), iou_threshold=0.3, centroid_distance_px=60.0)
    ids = {tracker.detect_and_track(FRAME)[0].track_id for _ in range(6)}
    assert len(ids) == 1


def test_no_match_across_classes() -> None:
    # Same position, class changes: must start a new track, not reuse the id.
    frames = [[det(10, 50, class_id=0)], [det(10, 50, class_id=1)]]
    tracker = IouTracker(ScriptedBackend(frames), max_coast_frames=0)
    first = tracker.detect_and_track(FRAME)
    second = tracker.detect_and_track(FRAME)
    assert first[0].track_id != [t for t in second if not t.coasting][0].track_id


def test_far_detection_gets_new_id() -> None:
    frames = [[det(10, 50)], [det(300, 300)]]
    tracker = IouTracker(ScriptedBackend(frames), centroid_distance_px=60.0)
    first = tracker.detect_and_track(FRAME)
    second = tracker.detect_and_track(FRAME)
    new_tracks = [t for t in second if not t.coasting]
    assert first[0].track_id == 1
    assert [t.track_id for t in new_tracks] == [2]
