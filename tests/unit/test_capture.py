from __future__ import annotations

import itertools
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np
import pytest

from tests.tools.make_synthetic_video import generate_scenario
from toskana.vision.capture import SourceOpenError, VideoSource


@pytest.fixture(scope="module")
def single_drink_video(tmp_path_factory: pytest.TempPathFactory) -> Path:
    generated = generate_scenario("single_drink", tmp_path_factory.mktemp("videos"))
    return generated.video_paths["cam"]


def test_reads_exact_frame_count(single_drink_video: Path) -> None:
    with VideoSource(str(single_drink_video), "file", paced=False) as source:
        frames = list(source)
    assert len(frames) == 60
    assert [index for index, _, _ in frames] == list(range(60))
    timestamps = [ts for _, ts, _ in frames]
    assert timestamps == sorted(timestamps)
    for _, _, frame in frames:
        assert frame.shape == (360, 640, 3)


def test_frame_size_and_fps_properties(single_drink_video: Path) -> None:
    with VideoSource(str(single_drink_video), "file", paced=False) as source:
        assert source.frame_size == (640, 360)
        assert source.fps == pytest.approx(15.0, abs=0.1)
        assert not source.is_live


def test_loop_mode_rewinds(single_drink_video: Path) -> None:
    with VideoSource(str(single_drink_video), "file", paced=False, loop=True) as source:
        frames = list(itertools.islice(iter(source), 70))
    assert [index for index, _, _ in frames] == list(range(70))


def test_paced_mode_holds_native_fps(single_drink_video: Path) -> None:
    n = 16
    with VideoSource(str(single_drink_video), "file", paced=True) as source:
        start = time.monotonic()
        frames = list(itertools.islice(iter(source), n))
        elapsed = time.monotonic() - start
    assert len(frames) == n
    measured_fps = (n - 1) / elapsed
    assert 15.0 * 0.8 <= measured_fps <= 15.0 * 1.2


def test_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(SourceOpenError):
        VideoSource(str(tmp_path / "does-not-exist.mp4"), "file")


def test_invalid_source_type_rejected() -> None:
    with pytest.raises(ValueError, match="source_type"):
        VideoSource("x", "webcam")


# --------------------------------------------------------------------------
# Reconnect / gap logic with mocked captures
# --------------------------------------------------------------------------


class FakeCapture:
    """cv2.VideoCapture stand-in yielding N frames, then failing reads."""

    def __init__(self, good_frames: int, opened: bool = True) -> None:
        self._remaining = good_frames
        self._opened = opened

    def isOpened(self) -> bool:  # noqa: N802 (OpenCV API name)
        return self._opened

    def read(self) -> tuple[bool, np.ndarray | None]:
        if not self._opened or self._remaining <= 0:
            return False, None
        self._remaining -= 1
        return True, np.zeros((4, 4, 3), np.uint8)

    def release(self) -> None:
        self._opened = False

    def get(self, prop: int) -> float:
        return 0.0

    def set(self, prop: int, value: float) -> bool:
        return False


def make_factory(captures: list[FakeCapture]) -> Callable[[], FakeCapture]:
    queue = list(captures)

    def factory() -> FakeCapture:
        if queue:
            return queue.pop(0)
        return FakeCapture(0, opened=False)  # further opens fail

    return factory


def test_gap_callback_fires_on_reconnect_and_on_give_up() -> None:
    gaps: list[tuple[float, float, str]] = []
    source = VideoSource(
        "rtsp://fake",
        "rtsp",
        max_retries=3,
        backoff_base_s=0.001,
        backoff_max_s=0.002,
        on_gap=lambda from_ts, to_ts, reason: gaps.append((from_ts, to_ts, reason)),
        capture_factory=make_factory([FakeCapture(3), FakeCapture(5)]),
    )
    frames = list(source)
    source.close()

    # 3 frames from the first capture + 5 after one reconnect.
    assert len(frames) == 8
    assert [index for index, _, _ in frames] == list(range(8))
    assert len(gaps) == 2
    reconnect_gap, give_up_gap = gaps
    assert "reconnected" in reconnect_gap[2]
    assert reconnect_gap[0] < reconnect_gap[1]
    assert "failed" in give_up_gap[2]
    assert give_up_gap[0] < give_up_gap[1]


def test_reconnect_gives_up_after_max_retries() -> None:
    gaps: list[str] = []
    source = VideoSource(
        "rtsp://fake",
        "rtsp",
        max_retries=2,
        backoff_base_s=0.001,
        on_gap=lambda _from, _to, reason: gaps.append(reason),
        capture_factory=make_factory([FakeCapture(1)]),
    )
    frames = list(source)
    assert len(frames) == 1
    assert gaps == ["reconnect failed after 2 attempt(s)"]


def test_file_source_does_not_reconnect_at_eof() -> None:
    gaps: list[str] = []
    source = VideoSource(
        "fake.mp4",
        "file",
        paced=False,
        on_gap=lambda _from, _to, reason: gaps.append(reason),
        capture_factory=make_factory([FakeCapture(4)]),
    )
    frames = list(source)
    assert len(frames) == 4
    assert gaps == []  # EOF on a file is not an outage


def test_usb_source_is_live_and_reconnects() -> None:
    gaps: list[str] = []
    source = VideoSource(
        0,
        "usb",
        max_retries=1,
        backoff_base_s=0.001,
        on_gap=lambda _from, _to, reason: gaps.append(reason),
        capture_factory=make_factory([FakeCapture(2), FakeCapture(2)]),
    )
    assert source.is_live
    frames = list(source)
    assert len(frames) == 4
    assert any("reconnected" in reason for reason in gaps)
