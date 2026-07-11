"""Render synthetic scenarios to video files + ``ground_truth.json``.

Importable API (used by the test suite) and CLI:

    python -m tests.tools.make_synthetic_video --scenario all --out-dir data/synthetic

Everything is deterministic: the same call produces byte-identical
``ground_truth.json`` and identical frame content (integer-deterministic
OpenCV drawing, no randomness).

The container/codec is probed at runtime: ``mp4v``/``.mp4`` preferred,
falling back to ``MJPG``/``.avi`` when mp4v is unavailable.
"""

from __future__ import annotations

import argparse
import functools
import json
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from tests.tools.scenarios import (
    BACKGROUND_BGR,
    COLOR_CLASS,
    SCENARIOS,
    Crossing,
    ObjectSpec,
    ScenarioSpec,
    TwoCamScenario,
)

_CODEC_CANDIDATES: tuple[tuple[str, str], ...] = (("mp4v", ".mp4"), ("MJPG", ".avi"))


@functools.lru_cache(maxsize=1)
def pick_codec() -> tuple[str, str]:
    """Return a ``(fourcc, extension)`` pair verified to work at runtime."""
    for fourcc, ext in _CODEC_CANDIDATES:
        if _codec_works(fourcc, ext):
            return fourcc, ext
    raise RuntimeError(f"no working OpenCV video codec found (tried {_CODEC_CANDIDATES})")


def _codec_works(fourcc: str, ext: str) -> bool:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / f"probe{ext}"
        writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*fourcc), 15.0, (64, 64))
        if not writer.isOpened():
            writer.release()
            return False
        for _ in range(2):
            writer.write(np.zeros((64, 64, 3), np.uint8))
        writer.release()
        cap = cv2.VideoCapture(str(path))
        ok = cap.isOpened() and cap.read()[0]
        cap.release()
        return bool(ok)


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def render_frame(spec: ScenarioSpec, frame_index: int) -> np.ndarray:
    """Draw one frame of a scenario (deterministic integer drawing)."""
    frame = np.full((spec.height, spec.width, 3), BACKGROUND_BGR, np.uint8)
    for obj in spec.objects:
        if not obj.is_visible(frame_index):
            continue
        center = obj.center_at(frame_index)
        assert center is not None
        cx, cy = int(round(center[0])), int(round(center[1]))
        color = COLOR_CLASS[obj.class_key]
        half = obj.size // 2
        if obj.shape == "circle":
            cv2.circle(frame, (cx, cy), half, color, -1)
        else:
            cv2.rectangle(frame, (cx - half, cy - half), (cx + half, cy + half), color, -1)
    return frame


def iter_frames(spec: ScenarioSpec) -> Iterator[np.ndarray]:
    for frame_index in range(spec.num_frames):
        yield render_frame(spec, frame_index)


def write_video(spec: ScenarioSpec, out_path: Path) -> Path:
    """Encode a scenario to ``out_path`` (extension set by the codec probe)."""
    fourcc, ext = pick_codec()
    out_path = out_path.with_suffix(ext)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*fourcc), float(spec.fps), (spec.width, spec.height)
    )
    if not writer.isOpened():
        raise RuntimeError(f"cannot open VideoWriter for {out_path}")
    for frame in iter_frames(spec):
        writer.write(frame)
    writer.release()
    return out_path


# --------------------------------------------------------------------------
# Ground truth
# --------------------------------------------------------------------------


def _crossing_dict(crossing: Crossing, fps: int) -> dict[str, Any]:
    return {
        "object_id": crossing.object_id,
        "class_key": crossing.class_key,
        "direction": crossing.direction,
        "crossing_frame": crossing.crossing_frame,
        "crossing_ts_ms": round(crossing.crossing_frame * 1000 / fps),
    }


def _object_dict(obj: ObjectSpec) -> dict[str, Any]:
    path = {}
    for frame in range(obj.first_frame, obj.last_frame + 1):
        center = obj.center_at(frame)
        assert center is not None
        path[str(frame)] = [round(center[0], 2), round(center[1], 2)]
    return {
        "object_id": obj.object_id,
        "class_key": obj.class_key,
        "shape": obj.shape,
        "size": obj.size,
        "hidden_frames": sorted(obj.hidden_frames),
        "path": path,
    }


def spec_ground_truth(spec: ScenarioSpec) -> dict[str, Any]:
    """Ground truth for one camera's video."""
    return {
        "video": {
            "fps": spec.fps,
            "width": spec.width,
            "height": spec.height,
            "num_frames": spec.num_frames,
            "mirrored": spec.mirrored,
            "line": {
                "orientation": "vertical",
                "x_norm": spec.line_x_norm,
                "positive_direction": "left_to_right",
            },
        },
        "crossings": [_crossing_dict(c, spec.fps) for c in spec.crossings],
        "objects": [_object_dict(obj) for obj in spec.objects],
    }


def dumps_deterministic(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode("utf-8")


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class GeneratedScenario:
    """Paths and parsed ground truth of one generated scenario."""

    name: str
    video_paths: dict[str, Path]  # cam key ("cam", "cam_a", "cam_b") -> file
    ground_truth_path: Path
    ground_truth: dict[str, Any]


def generate_scenario(name: str, out_dir: str | Path) -> GeneratedScenario:
    """Render scenario ``name`` into ``out_dir/name/`` (video(s) + GT json)."""
    if name not in SCENARIOS:
        raise KeyError(f"unknown scenario {name!r}; available: {sorted(SCENARIOS)}")
    scenario = SCENARIOS[name]()
    scenario_dir = Path(out_dir) / name
    scenario_dir.mkdir(parents=True, exist_ok=True)

    video_paths: dict[str, Path] = {}
    if isinstance(scenario, TwoCamScenario):
        cams_gt: dict[str, Any] = {}
        for cam_key, spec in scenario.cams.items():
            path = write_video(spec, scenario_dir / cam_key)
            video_paths[cam_key] = path
            cams_gt[cam_key] = {"video_file": path.name, **spec_ground_truth(spec)}
        fps = next(iter(scenario.cams.values())).fps
        ground_truth: dict[str, Any] = {
            "scenario": name,
            "canonical_crossings": [_crossing_dict(c, fps) for c in scenario.canonical_crossings],
            "cams": cams_gt,
        }
    else:
        path = write_video(scenario, scenario_dir / name)
        video_paths["cam"] = path
        ground_truth = {"scenario": name, "video_file": path.name, **spec_ground_truth(scenario)}

    ground_truth_path = scenario_dir / "ground_truth.json"
    ground_truth_path.write_bytes(dumps_deterministic(ground_truth))
    return GeneratedScenario(
        name=name,
        video_paths=video_paths,
        ground_truth_path=ground_truth_path,
        ground_truth=ground_truth,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate deterministic synthetic scenario videos + ground truth."
    )
    parser.add_argument(
        "--scenario",
        default="all",
        choices=[*sorted(SCENARIOS), "all"],
        help="scenario to generate (default: all)",
    )
    parser.add_argument(
        "--out-dir",
        default="./data/synthetic",
        help="output directory (a subdirectory per scenario is created)",
    )
    args = parser.parse_args(argv)
    names = sorted(SCENARIOS) if args.scenario == "all" else [args.scenario]
    fourcc, ext = pick_codec()
    print(f"codec: {fourcc} ({ext})")
    for name in names:
        generated = generate_scenario(name, args.out_dir)
        videos = ", ".join(str(p) for p in generated.video_paths.values())
        print(f"{name}: {videos} + {generated.ground_truth_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
