from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import pytest

from tests.tools import scenarios as sc
from tests.tools.make_synthetic_video import generate_scenario, pick_codec


def _decode_frames(path: Path) -> list:
    cap = cv2.VideoCapture(str(path))
    assert cap.isOpened()
    frames = []
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        frames.append(frame)
    cap.release()
    return frames


def _sampled_frame_hashes(path: Path, step: int = 7) -> list[str]:
    frames = _decode_frames(path)
    return [hashlib.sha256(frames[i].tobytes()).hexdigest() for i in range(0, len(frames), step)]


def test_codec_available() -> None:
    fourcc, ext = pick_codec()
    assert (fourcc, ext) in {("mp4v", ".mp4"), ("MJPG", ".avi")}


def test_generator_is_deterministic(tmp_path: Path) -> None:
    first = generate_scenario("tray_carry_3", tmp_path / "a")
    second = generate_scenario("tray_carry_3", tmp_path / "b")
    assert first.ground_truth_path.read_bytes() == second.ground_truth_path.read_bytes()
    assert _sampled_frame_hashes(first.video_paths["cam"]) == _sampled_frame_hashes(
        second.video_paths["cam"]
    )


def test_video_frame_count_matches_ground_truth(tmp_path: Path) -> None:
    generated = generate_scenario("single_drink", tmp_path)
    meta = generated.ground_truth["video"]
    frames = _decode_frames(generated.video_paths["cam"])
    assert len(frames) == meta["num_frames"] == 60
    assert frames[0].shape == (meta["height"], meta["width"], 3) == (360, 640, 3)
    assert meta["fps"] == 15
    assert meta["line"] == {
        "orientation": "vertical",
        "x_norm": 0.5,
        "positive_direction": "left_to_right",
    }


def test_ground_truth_json_is_valid_and_complete(tmp_path: Path) -> None:
    generated = generate_scenario("occlusion_gap", tmp_path)
    gt = json.loads(generated.ground_truth_path.read_text())
    assert gt == generated.ground_truth
    (crossing,) = gt["crossings"]
    assert set(crossing) == {
        "object_id",
        "class_key",
        "direction",
        "crossing_frame",
        "crossing_ts_ms",
    }
    assert crossing["crossing_ts_ms"] == round(crossing["crossing_frame"] * 1000 / 15)
    (obj,) = gt["objects"]
    assert obj["hidden_frames"] == [18, 19, 20, 21, 22]
    # Full per-frame path is present for debugging, including hidden frames.
    assert set(obj["path"]) == {str(f) for f in range(60)}


# -- scenario-level ground truth expectations -------------------------------


def test_single_drink_spec() -> None:
    spec = sc.single_drink()
    assert isinstance(spec, sc.ScenarioSpec)
    (crossing,) = spec.crossings
    assert crossing.class_key == "drink"
    assert crossing.direction == sc.POSITIVE


def test_tray_carry_3_spec() -> None:
    spec = sc.tray_carry_3()
    assert isinstance(spec, sc.ScenarioSpec)
    assert len(spec.objects) == 3
    assert [c.direction for c in spec.crossings] == [sc.POSITIVE] * 3
    assert sorted(c.class_key for c in spec.crossings) == ["drink", "main", "main"]
    frames = [c.crossing_frame for c in spec.crossings]
    assert max(frames) - min(frames) < spec.fps  # all within the same second


def test_reverse_return_spec() -> None:
    spec = sc.reverse_return()
    assert isinstance(spec, sc.ScenarioSpec)
    assert [c.direction for c in spec.crossings] == [sc.POSITIVE, sc.NEGATIVE]
    assert spec.crossings[0].crossing_frame < spec.crossings[1].crossing_frame


def test_loiter_on_line_spec() -> None:
    spec = sc.loiter_on_line()
    assert isinstance(spec, sc.ScenarioSpec)
    (crossing,) = spec.crossings
    assert crossing.direction == sc.POSITIVE
    # The raw path really does wiggle across the line beforehand.
    raw = sc.sign_change_crossings(spec.objects[0])
    assert len(raw) > 3
    assert raw[-1] == crossing


def test_occlusion_gap_spec() -> None:
    spec = sc.occlusion_gap()
    assert isinstance(spec, sc.ScenarioSpec)
    (obj,) = spec.objects
    hidden = sorted(obj.hidden_frames)
    assert len(hidden) == 5
    assert hidden == list(range(hidden[0], hidden[0] + 5))  # consecutive
    (crossing,) = spec.crossings
    assert crossing.direction == sc.POSITIVE
    assert max(hidden) < crossing.crossing_frame  # dropout happens before the line


def test_two_cam_same_exit_spec_and_generation(tmp_path: Path) -> None:
    scenario = sc.two_cam_same_exit()
    assert isinstance(scenario, sc.TwoCamScenario)
    assert len(scenario.canonical_crossings) == 2
    assert [c.class_key for c in scenario.canonical_crossings] == ["drink", "main"]
    delta = (
        scenario.canonical_crossings[1].crossing_frame
        - scenario.canonical_crossings[0].crossing_frame
    )
    assert delta == sc.FPS  # 1 s apart

    cam_a, cam_b = scenario.cams["cam_a"], scenario.cams["cam_b"]
    assert not cam_a.mirrored
    assert cam_b.mirrored
    for a, b in zip(cam_a.crossings, cam_b.crossings, strict=True):
        assert b.crossing_frame - a.crossing_frame == 3  # ~200 ms at 15 FPS
        assert b.direction == a.direction  # canonical direction, both cams

    generated = generate_scenario("two_cam_same_exit", tmp_path)
    assert set(generated.video_paths) == {"cam_a", "cam_b"}
    for cam_key, video_path in generated.video_paths.items():
        frames = _decode_frames(video_path)
        assert len(frames) == generated.ground_truth["cams"][cam_key]["video"]["num_frames"]
    assert len(generated.ground_truth["canonical_crossings"]) == 2

    # cam_b is mirrored: at its first frame the drink starts on the right.
    first_a = cam_a.objects[0].center_at(0)
    first_b = cam_b.objects[0].center_at(3)
    assert first_a is not None and first_b is not None
    assert first_b[0] == pytest.approx(sc.FRAME_WIDTH - first_a[0])
    assert first_b[1] == pytest.approx(first_a[1] + 25)


def test_center_never_exactly_on_line() -> None:
    # sign_change_crossings raises if a center lands exactly on the line;
    # constructing every scenario proves the paths are unambiguous.
    for factory in sc.SCENARIOS.values():
        factory()
