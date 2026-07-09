from __future__ import annotations

import math
from pathlib import Path

import numpy as np

from tests.tools import scenarios as sc
from tests.tools.make_synthetic_video import generate_scenario, render_frame
from toskana.vision.backends.synthetic import SYNTHETIC_CLASS_NAMES, SyntheticShapeDetector
from toskana.vision.detector import Detection

SINGLE_CAM_SCENARIOS = [
    "single_drink",
    "tray_carry_3",
    "reverse_return",
    "loiter_on_line",
    "occlusion_gap",
]


def _assert_detections_match_spec(
    spec: sc.ScenarioSpec, frame_index: int, detections: list[Detection], tolerance_px: float
) -> None:
    visible = [obj for obj in spec.objects if obj.is_visible(frame_index)]
    # No false positives on the background and no misses.
    assert len(detections) == len(visible), f"frame {frame_index}"
    remaining = list(detections)
    for obj in visible:
        center = obj.center_at(frame_index)
        assert center is not None
        best = min(remaining, key=lambda d: math.dist(d.center, center))
        assert math.dist(best.center, center) <= tolerance_px, (
            f"frame {frame_index}, object {obj.object_id}"
        )
        assert best.class_name == obj.class_key
        remaining.remove(best)


def test_class_names_match_color_map() -> None:
    detector = SyntheticShapeDetector()
    assert detector.class_names == SYNTHETIC_CLASS_NAMES == ["drink", "main", "dessert"]


def test_empty_background_has_no_detections() -> None:
    frame = np.full((360, 640, 3), sc.BACKGROUND_BGR, np.uint8)
    assert SyntheticShapeDetector().detect(frame) == []


def test_detects_all_scenarios_on_raw_frames() -> None:
    detector = SyntheticShapeDetector()
    for name in SINGLE_CAM_SCENARIOS:
        spec = sc.SCENARIOS[name]()
        assert isinstance(spec, sc.ScenarioSpec)
        for frame_index in range(spec.num_frames):
            frame = render_frame(spec, frame_index)
            detections = detector.detect(frame)
            _assert_detections_match_spec(spec, frame_index, detections, tolerance_px=3.0)


def test_detects_dessert_class() -> None:
    # No scenario uses dessert yet; verify the yellow HSV range directly.
    frame = np.full((360, 640, 3), sc.BACKGROUND_BGR, np.uint8)
    import cv2

    cv2.circle(frame, (200, 100), 20, sc.COLOR_CLASS["dessert"], -1)
    (det,) = SyntheticShapeDetector().detect(frame)
    assert det.class_name == "dessert"
    assert math.dist(det.center, (200, 100)) <= 1.0


def test_detects_through_video_compression(tmp_path: Path) -> None:
    """Boxes stay pixel-tight (<= 3 px center error) after encode/decode."""
    import cv2

    generated = generate_scenario("tray_carry_3", tmp_path)
    spec = sc.tray_carry_3()
    detector = SyntheticShapeDetector()
    cap = cv2.VideoCapture(str(generated.video_paths["cam"]))
    frame_index = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        detections = detector.detect(frame)
        _assert_detections_match_spec(spec, frame_index, detections, tolerance_px=3.0)
        frame_index += 1
    cap.release()
    assert frame_index == spec.num_frames


def test_bboxes_are_pixel_tight() -> None:
    spec = sc.single_drink()
    frame = render_frame(spec, 10)
    (det,) = SyntheticShapeDetector().detect(frame)
    # Circle of diameter 36 drawn with radius 18 -> bbox ~37 px wide.
    assert 34 <= det.width <= 40
    assert 34 <= det.height <= 40
