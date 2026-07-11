"""DriftDetector unit behavior: auto-calibration + persistence, alarm and
recovery transitions, bus publishing and the scipy-free SSIM fallback."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import numpy as np
import pytest

from toskana.events.bus import TOPIC_DRIFT, TOPIC_GAP, EventBus
from toskana.vision.drift import (
    COMPARE_SIZE,
    DriftDetector,
    prepare_frame,
    ssim_fallback,
)


def _frame(value: int, *, noise_seed: int | None = None) -> np.ndarray:
    frame = np.full((360, 640, 3), value, np.uint8)
    if noise_seed is not None:
        rng = np.random.default_rng(noise_seed)
        frame = rng.integers(0, 256, frame.shape, dtype=np.uint8)
    return frame


def _detector(directory: Path, bus: EventBus | None = None, **kwargs: Any) -> DriftDetector:
    kwargs.setdefault("consecutive_required", 2)
    kwargs.setdefault("check_interval_frames", 1)
    return DriftDetector(7, directory, restaurant_id=1, bus=bus, **kwargs)


class TestCalibration:
    def test_first_frame_autocalibrates_and_persists(self, tmp_path: Path) -> None:
        detector = _detector(tmp_path)
        assert detector.status.calibrated is False
        assert detector.status.drift_ok is None  # unknown before calibration
        assert detector.observe(_frame(200)) is None  # first frame = reference
        assert (tmp_path / "7.png").is_file()
        status = detector.status
        assert status.calibrated is True
        assert status.drift_ok is True
        assert status.drift_score is None  # no comparison ran yet

    def test_reference_survives_restart(self, tmp_path: Path) -> None:
        _detector(tmp_path).calibrate(_frame(0, noise_seed=1))
        reloaded = _detector(tmp_path)
        assert reloaded.status.calibrated is True
        # The reloaded reference matches: same scene scores far above threshold.
        score = reloaded.observe(_frame(0, noise_seed=1))
        assert score is not None and score > 0.95

    def test_invalid_parameters_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            DriftDetector(1, tmp_path, threshold=1.5)
        with pytest.raises(ValueError):
            DriftDetector(1, tmp_path, consecutive_required=0)


class TestAlarmTransitions:
    def test_alarm_needs_consecutive_failures_then_publishes(self, tmp_path: Path) -> None:
        bus = EventBus()
        drift_events: list[dict] = []
        gap_events: list[dict] = []
        bus.subscribe(TOPIC_DRIFT, drift_events.append)
        bus.subscribe(TOPIC_GAP, gap_events.append)
        detector = _detector(tmp_path, bus)
        detector.calibrate(_frame(0, noise_seed=1))

        blocked = _frame(0)  # lens covered: nothing like the reference
        score = detector.observe(blocked)
        assert score is not None and score < detector.threshold
        assert detector.status.drift_ok is True  # one failure is not an alarm
        assert drift_events == []

        detector.observe(blocked)  # second consecutive failure -> alarm
        status = detector.status
        assert status.drift_ok is False
        assert status.consecutive_failures == 2
        assert status.last_check_ts is not None
        (alarm,) = drift_events
        assert alarm["camera_id"] == 7 and alarm["restaurant_id"] == 1
        assert alarm["drift_ok"] is False
        assert alarm["score"] < detector.threshold
        (gap,) = gap_events  # the period's data is flagged as suspect
        assert gap["reason"] == "drift_alarm"
        assert gap["from_ts"] == gap["to_ts"]

    def test_recovery_and_recalibration_clear_the_alarm(self, tmp_path: Path) -> None:
        bus = EventBus()
        drift_events: list[dict] = []
        bus.subscribe(TOPIC_DRIFT, drift_events.append)
        detector = _detector(tmp_path, bus, consecutive_required=1)
        reference = _frame(0, noise_seed=1)
        detector.calibrate(reference)

        detector.observe(_frame(0))  # alarm
        assert detector.status.drift_ok is False
        detector.observe(reference)  # the view came back -> recovery published
        assert detector.status.drift_ok is True
        assert [event["drift_ok"] for event in drift_events] == [False, True]

        detector.observe(_frame(0))  # alarm again, on the moved camera
        assert detector.status.drift_ok is False
        detector.calibrate(_frame(0))  # operator accepts the new view
        assert detector.status.drift_ok is True
        assert [event["drift_ok"] for event in drift_events] == [False, True, False, True]

    def test_matching_view_never_alarms(self, tmp_path: Path) -> None:
        detector = _detector(tmp_path)
        reference = _frame(0, noise_seed=42)
        detector.calibrate(reference)
        for _ in range(5):
            score = detector.observe(reference)
            assert score is not None and score > 0.95
        assert detector.status.drift_ok is True
        assert detector.status.consecutive_failures == 0


class TestSsim:
    def test_fallback_identity_and_discrimination(self) -> None:
        rng = np.random.default_rng(3)
        a = rng.integers(0, 256, COMPARE_SIZE[::-1], dtype=np.uint8)
        b = rng.integers(0, 256, COMPARE_SIZE[::-1], dtype=np.uint8)
        assert ssim_fallback(a, a) == pytest.approx(1.0, abs=1e-6)
        assert ssim_fallback(a, b) < 0.2
        with pytest.raises(ValueError):
            ssim_fallback(a, a[:1])

    def test_prepare_frame_grayscale_and_size(self) -> None:
        prepared = prepare_frame(_frame(128, noise_seed=5))
        assert prepared.shape == COMPARE_SIZE[::-1]  # (h, w)
        assert prepared.dtype == np.uint8
