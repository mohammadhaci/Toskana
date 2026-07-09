"""Full-clip integration: generator -> encoded video -> VideoSource ->
SyntheticShapeDetector -> IouTracker, asserted against ground truth."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from tests.tools import scenarios as sc
from tests.tools.make_synthetic_video import generate_scenario
from toskana.vision.backends.synthetic import SyntheticShapeDetector
from toskana.vision.capture import VideoSource
from toskana.vision.detector import TrackedDetection
from toskana.vision.tracker import IouTracker

MATCH_TOLERANCE_PX = 5.0  # generous vs. the 3 px detector bound


def run_pipeline(name: str, out_dir: Path) -> tuple[sc.ScenarioSpec, list[list[TrackedDetection]]]:
    """Generate scenario ``name`` and track it over the full encoded clip."""
    generated = generate_scenario(name, out_dir)
    spec = sc.SCENARIOS[name]()
    assert isinstance(spec, sc.ScenarioSpec)
    tracker = IouTracker(SyntheticShapeDetector())
    per_frame: list[list[TrackedDetection]] = []
    with VideoSource(str(generated.video_paths["cam"]), "file", paced=False) as source:
        for _index, _ts, frame in source:
            per_frame.append(tracker.detect_and_track(frame))
    assert len(per_frame) == spec.num_frames
    return spec, per_frame


def match_tracks_to_ground_truth(
    spec: sc.ScenarioSpec, per_frame: list[list[TrackedDetection]]
) -> tuple[dict[str, set[int]], dict[int, set[str]]]:
    """Associate every non-coasting tracked detection with its GT object."""
    object_to_ids: dict[str, set[int]] = {obj.object_id: set() for obj in spec.objects}
    id_to_objects: dict[int, set[str]] = {}
    for frame_index, tracked in enumerate(per_frame):
        fresh = [t for t in tracked if not t.coasting]
        visible = [obj for obj in spec.objects if obj.is_visible(frame_index)]
        assert len(fresh) == len(visible), f"frame {frame_index}"
        for obj in visible:
            center = obj.center_at(frame_index)
            assert center is not None
            best = min(fresh, key=lambda t: math.dist(t.center, center))
            assert math.dist(best.center, center) <= MATCH_TOLERANCE_PX
            assert best.class_name == obj.class_key
            object_to_ids[obj.object_id].add(best.track_id)
            id_to_objects.setdefault(best.track_id, set()).add(obj.object_id)
            fresh.remove(best)
    return object_to_ids, id_to_objects


def test_tray_carry_3_three_ids_zero_switches(tmp_path: Path) -> None:
    """Release gate: 3 items on one tray = exactly 3 tracks, no id switches."""
    spec, per_frame = run_pipeline("tray_carry_3", tmp_path)
    object_to_ids, id_to_objects = match_tracks_to_ground_truth(spec, per_frame)

    all_ids = {t.track_id for tracked in per_frame for t in tracked}
    assert len(all_ids) == 3  # exactly 3 distinct track ids over the whole clip
    for object_id, ids in object_to_ids.items():
        assert len(ids) == 1, f"{object_id} switched track ids: {ids}"  # zero switches
    for track_id, objects in id_to_objects.items():
        assert len(objects) == 1, f"track {track_id} jumped between objects: {objects}"


def test_occlusion_gap_same_id_through_dropout(tmp_path: Path) -> None:
    spec, per_frame = run_pipeline("occlusion_gap", tmp_path)
    object_to_ids, _ = match_tracks_to_ground_truth(spec, per_frame)
    (obj,) = spec.objects
    hidden = sorted(obj.hidden_frames)

    (track_id,) = object_to_ids[obj.object_id]  # one id before AND after the gap
    all_ids = {t.track_id for tracked in per_frame for t in tracked}
    assert all_ids == {track_id}
    # During the dropout the tracker coasts the same identity.
    for frame_index in hidden:
        (coasted,) = per_frame[frame_index]
        assert coasted.coasting
        assert coasted.track_id == track_id
    # Fresh detection right after the gap keeps the id.
    after = [t for t in per_frame[hidden[-1] + 1] if not t.coasting]
    assert [t.track_id for t in after] == [track_id]


@pytest.mark.parametrize("name", ["single_drink", "reverse_return", "loiter_on_line"])
def test_single_object_scenarios_keep_one_id(name: str, tmp_path: Path) -> None:
    spec, per_frame = run_pipeline(name, tmp_path)
    object_to_ids, _ = match_tracks_to_ground_truth(spec, per_frame)
    all_ids = {t.track_id for tracked in per_frame for t in tracked}
    assert len(all_ids) == 1
    (obj,) = spec.objects
    assert object_to_ids[obj.object_id] == all_ids
