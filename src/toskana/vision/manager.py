"""Owns one :class:`~toskana.vision.pipeline.CameraPipeline` thread per camera.

The manager is the bridge between the DB configuration (cameras, lines,
class mappings, model registry) and the headless vision pipelines:

* builds a :class:`~toskana.vision.pipeline.PipelineSpec` per enabled camera
  of the active restaurant,
* runs each pipeline in a supervised thread (errors are captured, not lost),
* keeps a thread-safe latest *annotated* JPEG per camera (line + track boxes
  + live counts) for the MJPEG stream and snapshot endpoints,
* records ``data_gaps`` marker rows (``pipeline_start`` / ``pipeline_stop``)
  per camera session via the event bus,
* supports start/stop/restart and :meth:`reload_camera` for hot line/config
  changes (the pipeline is rebuilt from fresh DB rows).
"""

from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.models import Camera, Category, ClassMapping, Line, ModelRegistry, Restaurant
from toskana.events.bus import TOPIC_GAP, EventBus
from toskana.vision.detector import TrackedDetection
from toskana.vision.line_crossing import LineSpec
from toskana.vision.mapping import MappingRule
from toskana.vision.overlay import encode_jpeg, render_live
from toskana.vision.pipeline import CameraPipeline, PipelineSpec

logger = logging.getLogger(__name__)

#: Minimum seconds between two preview JPEG encodes (~10 fps cap).
_PREVIEW_MIN_INTERVAL_S = 0.09
#: Sliding window size for the measured-FPS estimate.
_FPS_WINDOW = 30


class CameraNotRegistered(KeyError):
    """The camera id is unknown to the database."""


class CameraAlreadyRunning(RuntimeError):
    """start requested for a camera whose pipeline thread is alive."""


@dataclass
class _ManagedCamera:
    """Book-keeping for one camera's pipeline thread."""

    camera_id: int
    name: str
    spec: PipelineSpec
    pipeline: CameraPipeline
    thread: threading.Thread | None = None
    started_ts: int | None = None  # epoch ms
    last_error: str | None = None
    last_frame_ts: int | None = None  # epoch ms
    frame_times: deque[float] = field(default_factory=lambda: deque(maxlen=_FPS_WINDOW))
    #: (jpeg bytes, sequence number) — replaced atomically, read lock-free.
    latest: tuple[bytes, int] | None = None
    _last_encode_mono: float = 0.0

    @property
    def running(self) -> bool:
        return self.thread is not None and self.thread.is_alive()

    @property
    def measured_fps(self) -> float:
        if len(self.frame_times) < 2:
            return 0.0
        span = self.frame_times[-1] - self.frame_times[0]
        if span <= 0:
            return 0.0
        return round((len(self.frame_times) - 1) / span, 2)


class PipelineManager:
    """Start/stop/observe the per-camera counting pipelines of one site."""

    def __init__(
        self,
        config: AppConfig,
        *,
        bus: EventBus,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._config = config
        self._bus = bus
        self._session_factory = session_factory
        self._lock = threading.RLock()
        self._entries: dict[int, _ManagedCamera] = {}

    # -- lifecycle ------------------------------------------------------------

    def start_all(self) -> int:
        """Start every enabled camera of the active restaurant; returns count."""
        started = 0
        with self._session_factory() as session:
            restaurant = session.scalar(
                select(Restaurant).where(Restaurant.slug == self._config.active_restaurant_slug)
            )
            if restaurant is None:
                logger.warning(
                    "active restaurant %r not found; no pipelines started",
                    self._config.active_restaurant_slug,
                )
                return 0
            camera_ids = list(
                session.scalars(
                    select(Camera.id)
                    .where(Camera.restaurant_id == restaurant.id, Camera.enabled.is_(True))
                    .order_by(Camera.id)
                )
            )
        for camera_id in camera_ids:
            try:
                self.start_camera(camera_id)
                started += 1
            except Exception:  # noqa: BLE001 - one bad camera must not block the rest
                logger.exception("failed to start camera %s", camera_id)
        return started

    def stop_all(self, *, join_timeout_s: float = 5.0) -> None:
        with self._lock:
            camera_ids = list(self._entries)
        for camera_id in camera_ids:
            self.stop_camera(camera_id, join_timeout_s=join_timeout_s)

    # -- per-camera control -----------------------------------------------------

    def start_camera(self, camera_id: int) -> None:
        """Build a fresh spec from the DB and start the pipeline thread."""
        with self._lock:
            existing = self._entries.get(camera_id)
            if existing is not None and existing.running:
                raise CameraAlreadyRunning(f"camera {camera_id} is already running")
            with self._session_factory() as session:
                camera = session.get(Camera, camera_id)
                if camera is None:
                    raise CameraNotRegistered(camera_id)
                spec = self._build_spec(session, camera)
                name = camera.name
            entry = _ManagedCamera(camera_id=camera_id, name=name, spec=spec, pipeline=None)  # type: ignore[arg-type]
            entry.pipeline = CameraPipeline(
                spec,
                bus=self._bus,
                on_frame=self._make_frame_hook(entry),
            )
            entry.thread = threading.Thread(
                target=self._run_pipeline,
                args=(entry,),
                name=f"manager-cam{camera_id}",
                daemon=True,
            )
            entry.started_ts = int(time.time() * 1000)
            self._entries[camera_id] = entry
            entry.thread.start()

    def stop_camera(self, camera_id: int, *, join_timeout_s: float = 5.0) -> bool:
        """Request stop and wait for the thread; returns True if it was running."""
        with self._lock:
            entry = self._entries.get(camera_id)
        if entry is None:
            return False
        was_running = entry.running
        entry.pipeline.stop()
        if entry.thread is not None:
            entry.thread.join(join_timeout_s)
        return was_running

    def restart_camera(self, camera_id: int) -> None:
        self.stop_camera(camera_id)
        self.start_camera(camera_id)

    def reload_camera(self, camera_id: int) -> bool:
        """Hot config reload: restart the pipeline with fresh DB rows.

        No-op (returns False) when the camera is not currently running.
        """
        with self._lock:
            entry = self._entries.get(camera_id)
            if entry is None or not entry.running:
                return False
        self.restart_camera(camera_id)
        return True

    def reload_restaurant(self, restaurant_id: int) -> int:
        """Hot-reload every running camera of a restaurant (e.g. after a
        mapping-set or model activation change). Returns reload count."""
        with self._lock:
            camera_ids = [
                entry.camera_id
                for entry in self._entries.values()
                if entry.spec.restaurant_id == restaurant_id and entry.running
            ]
        return sum(1 for camera_id in camera_ids if self.reload_camera(camera_id))

    # -- observation --------------------------------------------------------------

    def is_running(self, camera_id: int) -> bool:
        with self._lock:
            entry = self._entries.get(camera_id)
        return entry is not None and entry.running

    def camera_status(self, camera_id: int) -> dict[str, Any] | None:
        with self._lock:
            entry = self._entries.get(camera_id)
        if entry is None:
            return None
        return self._entry_status(entry)

    def status(self) -> list[dict[str, Any]]:
        with self._lock:
            entries = list(self._entries.values())
        return [self._entry_status(entry) for entry in entries]

    def latest_jpeg(self, camera_id: int) -> tuple[bytes, int] | None:
        """Latest annotated preview frame as ``(jpeg_bytes, sequence)``."""
        with self._lock:
            entry = self._entries.get(camera_id)
        if entry is None:
            return None
        return entry.latest

    @staticmethod
    def _entry_status(entry: _ManagedCamera) -> dict[str, Any]:
        return {
            "camera_id": entry.camera_id,
            "name": entry.name,
            "running": entry.running,
            "frames": entry.pipeline.result.frames,
            "fps": entry.measured_fps,
            "last_frame_ts": entry.last_frame_ts,
            "gaps": entry.pipeline.result.gaps,
            "backend": entry.spec.backend,
            "device": entry.spec.device,
            "source_type": entry.spec.source_type,
            "started_ts": entry.started_ts,
            "last_error": entry.last_error,
        }

    # -- pipeline thread -----------------------------------------------------------

    def _run_pipeline(self, entry: _ManagedCamera) -> None:
        self._publish_marker(entry, "pipeline_start")
        try:
            entry.pipeline.run_once()
        except Exception as exc:  # noqa: BLE001 - keep the error observable in status()
            entry.last_error = f"{type(exc).__name__}: {exc}"
            logger.exception("pipeline for camera %s crashed", entry.camera_id)
        finally:
            self._publish_marker(entry, "pipeline_stop")

    def _publish_marker(self, entry: _ManagedCamera, reason: str) -> None:
        """Record a zero-length data_gaps marker row for the camera session."""
        now_ms = int(time.time() * 1000)
        self._bus.publish(
            TOPIC_GAP,
            {
                "restaurant_id": entry.spec.restaurant_id,
                "camera_id": entry.camera_id,
                "from_ts": now_ms,
                "to_ts": now_ms,
                "reason": reason,
            },
        )

    def _make_frame_hook(self, entry: _ManagedCamera) -> Any:
        def on_frame(
            frame_index: int, ts_ms: int, frame: np.ndarray, tracked: list[TrackedDetection]
        ) -> None:
            now_mono = time.monotonic()
            entry.frame_times.append(now_mono)
            entry.last_frame_ts = int(time.time() * 1000)
            if now_mono - entry._last_encode_mono < _PREVIEW_MIN_INTERVAL_S:
                return  # cap preview encoding at ~10 fps
            entry._last_encode_mono = now_mono
            lines_px = [(counter.a, counter.b) for counter in entry.pipeline.counters]
            annotated = render_live(frame, tracked, lines_px, entry.pipeline.result.counts)
            sequence = entry.latest[1] + 1 if entry.latest is not None else 1
            entry.latest = (encode_jpeg(annotated), sequence)

        return on_frame

    # -- spec building ---------------------------------------------------------------

    def _build_spec(self, session: Session, camera: Camera) -> PipelineSpec:
        restaurant = session.get(Restaurant, camera.restaurant_id)
        assert restaurant is not None  # FK guarantees it
        lines = tuple(
            LineSpec.from_row(row)
            for row in session.scalars(
                select(Line)
                .where(Line.camera_id == camera.id, Line.enabled.is_(True))
                .order_by(Line.id)
            )
        )
        backend = self._config.detector_backend
        model = self._effective_model(session, camera)
        if backend == "synthetic":
            rules = self._synthetic_rules(session, camera.restaurant_id)
        else:
            rules = self._model_rules(session, camera.restaurant_id, model)
        source: str | int = camera.source_url
        if camera.source_type == "usb":
            source = int(camera.source_url)
        is_file = camera.source_type == "file"
        return PipelineSpec(
            restaurant_id=camera.restaurant_id,
            camera_id=camera.id,
            source=source,
            restaurant_slug=restaurant.slug,
            source_type=camera.source_type,
            backend=backend,
            model_path=model.path if model is not None else "yolov8n.pt",
            device=self._config.device,
            paced=is_file,  # replay files at native FPS (live preview / demo)
            loop=is_file and self._config.loop_file_sources,
            lines=lines,
            mapping_rules=rules,
            snapshots_dir=self._config.snapshots_dir,
        )

    @staticmethod
    def _effective_model(session: Session, camera: Camera) -> ModelRegistry | None:
        """Camera override model, else the restaurant's (or shared) active model."""
        if camera.model_id is not None:
            return session.get(ModelRegistry, camera.model_id)
        active = session.scalars(
            select(ModelRegistry).where(
                ModelRegistry.is_active.is_(True),
                (ModelRegistry.restaurant_id == camera.restaurant_id)
                | (ModelRegistry.restaurant_id.is_(None)),
            )
        ).all()
        # Prefer a restaurant-owned active model over a shared one.
        for row in active:
            if row.restaurant_id == camera.restaurant_id:
                return row
        return active[0] if active else None

    @staticmethod
    def _synthetic_rules(session: Session, restaurant_id: int) -> tuple[MappingRule, ...]:
        """Map synthetic class names onto same-key categories (demo/CI mode)."""
        from toskana.vision.backends.synthetic import SYNTHETIC_CLASS_NAMES

        rules: list[MappingRule] = []
        for class_id, class_name in enumerate(SYNTHETIC_CLASS_NAMES):
            category = session.scalar(
                select(Category).where(
                    Category.restaurant_id == restaurant_id, Category.key == class_name
                )
            )
            if category is not None:
                rules.append(
                    MappingRule(
                        model_class_id=class_id,
                        model_class_name=class_name,
                        category_id=category.id,
                        min_confidence=0.0,
                    )
                )
        return tuple(rules)

    @staticmethod
    def _model_rules(
        session: Session, restaurant_id: int, model: ModelRegistry | None
    ) -> tuple[MappingRule, ...]:
        if model is None:
            return ()
        rows = session.scalars(
            select(ClassMapping).where(
                ClassMapping.restaurant_id == restaurant_id,
                ClassMapping.model_id == model.id,
            )
        ).all()
        return tuple(MappingRule.from_row(row) for row in rows)
