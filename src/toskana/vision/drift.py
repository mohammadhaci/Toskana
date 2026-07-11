"""Camera drift detection: periodic SSIM against a calibration snapshot.

When a camera is (accidentally) moved, rotated or blocked, its counting line
no longer matches the physical pass — counts silently degrade. The
:class:`DriftDetector` guards against this:

* On camera start the first frame is stored as the **calibration
  reference** (grayscale, downscaled) under
  ``{snapshots_dir}/calibration/{camera_id}.png`` — unless a reference
  already exists on disk, so restarts keep comparing against the original
  view. ``POST /api/cameras/{id}/calibrate`` re-captures it deliberately.
* Every ``check_interval_frames`` processed frames (default ≈ 5 minutes of
  video) the current frame is compared to the reference via SSIM
  (``skimage.metrics.structural_similarity`` when installed, an equivalent
  OpenCV implementation otherwise — the core package does not require
  scipy).
* A score below ``threshold`` (default 0.55) for ``consecutive_required``
  consecutive checks raises the **drift alarm**: ``drift_ok`` flips to
  False in the camera status / ``/api/system/health``, a ``drift`` bus
  event is published (forwarded on ``/ws/live`` as ``{type: "drift", ...}``)
  and a zero-length ``data_gaps`` marker (reason ``drift_alarm``) flags the
  period's data as suspect in reports. Recovery or recalibration resets the
  alarm (also published, so dashboards can clear the warning).

Thread-safety: ``observe`` runs on the camera's pipeline thread while
``calibrate``/``status`` are called from API threads — all state is guarded
by one lock; bus publishing happens outside it.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from toskana.events.bus import TOPIC_DRIFT, TOPIC_GAP, EventBus

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLD = 0.55
DEFAULT_CONSECUTIVE_REQUIRED = 3
#: ~5 minutes of video at the default 15 FPS.
DEFAULT_CHECK_INTERVAL_FRAMES = 4500

#: Comparison resolution (grayscale): small enough to be cheap, large enough
#: to catch rotations/shifts/blockage.
COMPARE_SIZE = (160, 90)  # (width, height)


def prepare_frame(frame_bgr: np.ndarray) -> np.ndarray:
    """Grayscale + fixed downscale — the representation SSIM runs on."""
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if frame_bgr.ndim == 3 else frame_bgr
    return cv2.resize(gray, COMPARE_SIZE, interpolation=cv2.INTER_AREA)


def ssim_score(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM between two equally-shaped uint8 grayscale images (range -1..1).

    Uses scikit-image when available, else the OpenCV/Gaussian fallback.
    """
    try:
        from skimage.metrics import structural_similarity
    except ImportError:
        return ssim_fallback(a, b)
    return float(structural_similarity(a, b, data_range=255))


def ssim_fallback(a: np.ndarray, b: np.ndarray) -> float:
    """Gaussian-weighted SSIM (standard constants), no scipy dependency."""
    if a.shape != b.shape:
        raise ValueError(f"shape mismatch: {a.shape} vs {b.shape}")
    c1 = (0.01 * 255) ** 2
    c2 = (0.03 * 255) ** 2
    x = a.astype(np.float64)
    y = b.astype(np.float64)

    def blur(img: np.ndarray) -> np.ndarray:
        return cv2.GaussianBlur(img, (11, 11), 1.5)

    mu_x, mu_y = blur(x), blur(y)
    mu_x2, mu_y2, mu_xy = mu_x * mu_x, mu_y * mu_y, mu_x * mu_y
    sigma_x2 = blur(x * x) - mu_x2
    sigma_y2 = blur(y * y) - mu_y2
    sigma_xy = blur(x * y) - mu_xy
    ssim_map = ((2 * mu_xy + c1) * (2 * sigma_xy + c2)) / (
        (mu_x2 + mu_y2 + c1) * (sigma_x2 + sigma_y2 + c2)
    )
    return float(ssim_map.mean())


@dataclass(frozen=True)
class DriftStatus:
    """Snapshot of one camera's drift state (drives status/health fields)."""

    calibrated: bool
    drift_ok: bool | None  # None = not calibrated yet
    drift_score: float | None  # last SSIM score (None before the first check)
    consecutive_failures: int
    last_check_ts: int | None  # epoch ms


class DriftDetector:
    """SSIM drift watchdog for one camera."""

    def __init__(
        self,
        camera_id: int,
        calibration_dir: str | Path,
        *,
        restaurant_id: int | None = None,
        bus: EventBus | None = None,
        threshold: float = DEFAULT_THRESHOLD,
        consecutive_required: int = DEFAULT_CONSECUTIVE_REQUIRED,
        check_interval_frames: int = DEFAULT_CHECK_INTERVAL_FRAMES,
    ) -> None:
        if not 0.0 < threshold < 1.0:
            raise ValueError("threshold must be in (0, 1)")
        if consecutive_required < 1 or check_interval_frames < 1:
            raise ValueError("consecutive_required and check_interval_frames must be >= 1")
        self.camera_id = camera_id
        self.restaurant_id = restaurant_id
        self.calibration_path = Path(calibration_dir) / f"{camera_id}.png"
        self.threshold = threshold
        self.consecutive_required = consecutive_required
        self.check_interval_frames = check_interval_frames
        self._bus = bus
        self._lock = threading.Lock()
        self._reference: np.ndarray | None = self._load_reference()
        self._frames_seen = 0
        self._consecutive_failures = 0
        self._alarmed = False
        self._last_score: float | None = None
        self._last_check_ts: int | None = None

    # -- calibration -----------------------------------------------------------

    def _load_reference(self) -> np.ndarray | None:
        if not self.calibration_path.is_file():
            return None
        reference = cv2.imread(str(self.calibration_path), cv2.IMREAD_GRAYSCALE)
        if reference is None:
            logger.warning("unreadable calibration snapshot: %s", self.calibration_path)
            return None
        if reference.shape[::-1] != COMPARE_SIZE:
            reference = cv2.resize(reference, COMPARE_SIZE, interpolation=cv2.INTER_AREA)
        return reference

    def calibrate(self, frame_bgr: np.ndarray) -> None:
        """Store ``frame_bgr`` as the new reference and reset the alarm."""
        prepared = prepare_frame(frame_bgr)
        self.calibration_path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(self.calibration_path), prepared):
            raise OSError(f"failed to write calibration snapshot: {self.calibration_path}")
        with self._lock:
            was_alarmed = self._alarmed
            self._reference = prepared
            self._consecutive_failures = 0
            self._alarmed = False
            self._last_score = None
        if was_alarmed:
            self._publish(1.0, alarmed=False)
        logger.info(
            "camera %s drift reference calibrated (%s)", self.camera_id, self.calibration_path
        )

    # -- observation (pipeline thread) --------------------------------------------

    def observe(self, frame_bgr: np.ndarray) -> float | None:
        """Feed one processed frame; returns the SSIM score when a check ran.

        The very first frame auto-calibrates when no reference exists yet.
        """
        with self._lock:
            if self._reference is None:
                needs_calibration = True
            else:
                needs_calibration = False
                self._frames_seen += 1
                due = self._frames_seen % self.check_interval_frames == 0
        if needs_calibration:
            self.calibrate(frame_bgr)
            return None
        if not due:
            return None
        return self._check(frame_bgr)

    def _check(self, frame_bgr: np.ndarray) -> float:
        with self._lock:
            reference = self._reference
        assert reference is not None
        score = ssim_score(prepare_frame(frame_bgr), reference)
        transition: bool | None = None  # True = alarm raised, False = recovered
        with self._lock:
            self._last_score = score
            self._last_check_ts = int(time.time() * 1000)
            if score < self.threshold:
                self._consecutive_failures += 1
                if not self._alarmed and self._consecutive_failures >= self.consecutive_required:
                    self._alarmed = True
                    transition = True
            else:
                if self._alarmed:
                    transition = False
                self._consecutive_failures = 0
                self._alarmed = False
        if transition is True:
            logger.warning(
                "camera %s DRIFT ALARM: SSIM %.3f < %.2f for %d consecutive checks",
                self.camera_id,
                score,
                self.threshold,
                self.consecutive_required,
            )
            self._publish(score, alarmed=True)
            self._publish_gap_marker()
        elif transition is False:
            logger.info("camera %s drift recovered (SSIM %.3f)", self.camera_id, score)
            self._publish(score, alarmed=False)
        return score

    # -- observability ----------------------------------------------------------

    @property
    def status(self) -> DriftStatus:
        with self._lock:
            calibrated = self._reference is not None
            return DriftStatus(
                calibrated=calibrated,
                drift_ok=None if not calibrated else not self._alarmed,
                drift_score=self._last_score,
                consecutive_failures=self._consecutive_failures,
                last_check_ts=self._last_check_ts,
            )

    # -- publishing ---------------------------------------------------------------

    def _publish(self, score: float, *, alarmed: bool) -> None:
        if self._bus is None:
            return
        payload: dict[str, Any] = {
            "camera_id": self.camera_id,
            "restaurant_id": self.restaurant_id,
            "score": round(score, 4),
            "drift_ok": not alarmed,
            "ts": int(time.time() * 1000),
        }
        self._bus.publish(TOPIC_DRIFT, payload)

    def _publish_gap_marker(self) -> None:
        """Flag the data as suspect: zero-length data_gaps marker row."""
        if self._bus is None or self.restaurant_id is None:
            return
        now_ms = int(time.time() * 1000)
        self._bus.publish(
            TOPIC_GAP,
            {
                "restaurant_id": self.restaurant_id,
                "camera_id": self.camera_id,
                "from_ts": now_ms,
                "to_ts": now_ms,
                "reason": "drift_alarm",
            },
        )
