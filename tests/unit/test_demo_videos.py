"""Demo clip generator: files created once, counted correctly end-to-end."""

from __future__ import annotations

from pathlib import Path

from toskana.demo_videos import _LOOP_FRAMES, ensure_demo_videos
from toskana.vision.capture import VideoSource


def test_generates_missing_and_skips_existing(tmp_path: Path) -> None:
    paths = ["data/videos/pass_left.mp4", "data/videos/pass_right.mp4"]

    created = ensure_demo_videos(paths, base_dir=tmp_path)
    assert [p.name for p in created] == ["pass_left.mp4", "pass_right.mp4"]
    for p in created:
        assert p.stat().st_size > 0

    assert ensure_demo_videos(paths, base_dir=tmp_path) == []  # idempotent


def test_generated_clip_is_readable_with_expected_length(tmp_path: Path) -> None:
    (path,) = ensure_demo_videos(["clip.mp4"], base_dir=tmp_path)
    with VideoSource(str(path), source_type="file", paced=False) as source:
        frames = sum(1 for _ in source)
    assert frames == _LOOP_FRAMES
