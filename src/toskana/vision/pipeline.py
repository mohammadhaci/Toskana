"""Headless per-camera counting pipeline.

Composes ``VideoSource -> TrackingBackend -> LineCrossingCounter(s) ->
ClassMappingResolver -> SnapshotSaver -> EventBus``. No database session and
no UI: everything arrives through a plain :class:`PipelineSpec` (assembled
elsewhere from config/DB rows) and leaves as bus payloads on the topics
``crossing`` and ``gap`` — the :class:`~toskana.events.writer.EventWriter`
persists them.

Run synchronously with :meth:`CameraPipeline.run_once` (tests, CLI) or in a
background thread via :meth:`start` / :meth:`stop` / :meth:`join`.

Timestamps: for file sources, event time advances with *media time*
(``start + frame_index / fps``) so cooldowns and dedup windows behave
identically whether the clip is replayed paced or unpaced; live sources use
the wall clock.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP, EventBus
from toskana.events.writer import new_event_id
from toskana.vision.capture import VideoSource
from toskana.vision.detector import TrackingBackend
from toskana.vision.line_crossing import CrossingEvent, LineCrossingCounter, LineSpec
from toskana.vision.mapping import ClassMappingResolver, MappingRule
from toskana.vision.snapshots import SnapshotSaver

logger = logging.getLogger(__name__)

#: counter direction -> ``events.direction`` column value.
DIRECTION_TO_DB = {"positive": "out", "negative": "in"}


@dataclass(frozen=True)
class PipelineSpec:
    """Plain configuration for one camera pipeline (no ORM objects)."""

    restaurant_id: int
    camera_id: int
    source: str | int
    restaurant_slug: str = "toskana"
    source_type: str = "file"  # file | usb | rtsp
    backend: str = "synthetic"  # synthetic | yolo
    model_path: str = "yolov8n.pt"  # yolo backend weights
    device: str = "auto"
    paced: bool = False
    loop: bool = False
    lines: tuple[LineSpec, ...] = ()
    mapping_rules: tuple[MappingRule, ...] = ()
    snapshots_dir: str | None = None


@dataclass
class PipelineResult:
    """Aggregate outcome of one pipeline run (also exposed live on the bus)."""

    frames: int = 0
    crossings: list[CrossingEvent] = field(default_factory=list)
    payloads: list[dict[str, Any]] = field(default_factory=list)
    #: counts[direction][class_name] with direction in {positive, negative}.
    counts: dict[str, dict[str, int]] = field(
        default_factory=lambda: {"positive": {}, "negative": {}}
    )
    gaps: int = 0

    def add(self, crossing: CrossingEvent, payload: dict[str, Any]) -> None:
        self.crossings.append(crossing)
        self.payloads.append(payload)
        per_class = self.counts[crossing.direction]
        per_class[crossing.class_name] = per_class.get(crossing.class_name, 0) + 1

    @property
    def total(self) -> int:
        return len(self.crossings)


def make_tracking_backend(spec: PipelineSpec) -> TrackingBackend:
    """Build the tracking backend named by ``spec.backend`` (yolo is lazy)."""
    if spec.backend == "synthetic":
        from toskana.vision.backends.synthetic import SyntheticShapeDetector
        from toskana.vision.tracker import IouTracker

        return IouTracker(SyntheticShapeDetector())
    if spec.backend == "yolo":
        from toskana.vision.backends.yolo import YoloBackend

        return YoloBackend(spec.model_path, device=spec.device)
    raise ValueError(f"unknown backend: {spec.backend!r}")


class CameraPipeline:
    """Counts line crossings on one video source, headless."""

    def __init__(
        self,
        spec: PipelineSpec,
        *,
        bus: EventBus | None = None,
        tracking_backend: TrackingBackend | None = None,
    ) -> None:
        self.spec = spec
        self.bus = bus
        self._backend = tracking_backend
        self._snapshot_saver = (
            SnapshotSaver(spec.snapshots_dir, spec.restaurant_slug)
            if spec.snapshots_dir is not None
            else None
        )
        self.counters: list[LineCrossingCounter] = []
        self.result = PipelineResult()
        self._stop_requested = threading.Event()
        self._thread: threading.Thread | None = None

    # -- threaded operation ---------------------------------------------------

    def start(self) -> None:
        """Run the pipeline in a background thread."""
        if self._thread is not None:
            raise RuntimeError("CameraPipeline already started")
        self._stop_requested.clear()
        self._thread = threading.Thread(
            target=self.run_once, name=f"pipeline-cam{self.spec.camera_id}", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # -- synchronous operation --------------------------------------------------

    def run_once(self) -> PipelineResult:
        """Process the source to exhaustion (or until :meth:`stop`)."""
        spec = self.spec
        backend = self._backend if self._backend is not None else make_tracking_backend(spec)
        resolver = ClassMappingResolver(spec.mapping_rules)
        result = self.result = PipelineResult()

        with VideoSource(
            spec.source,
            spec.source_type,
            paced=spec.paced,
            loop=spec.loop,
            on_gap=self._on_gap,
        ) as source:
            fps = source.fps or 15.0
            is_live = source.is_live
            start_epoch_ms = int(time.time() * 1000)
            for frame_index, _mono_ts, frame in source:
                if self._stop_requested.is_set():
                    break
                if not self.counters:
                    height, width = frame.shape[:2]
                    self.counters = [
                        LineCrossingCounter(line, width, height) for line in spec.lines
                    ]
                ts_ms = (
                    int(time.time() * 1000)
                    if is_live
                    else start_epoch_ms + round(frame_index * 1000 / fps)
                )
                tracked = backend.detect_and_track(frame)
                for counter in self.counters:
                    for crossing in counter.update(tracked, ts_ms=ts_ms, frame_index=frame_index):
                        self._emit(crossing, frame, counter, resolver, result)
                result.frames += 1
        return result

    # -- internals --------------------------------------------------------------

    def _emit(
        self,
        crossing: CrossingEvent,
        frame: Any,
        counter: LineCrossingCounter,
        resolver: ClassMappingResolver,
        result: PipelineResult,
    ) -> None:
        resolution = resolver.resolve(crossing.class_id, crossing.class_name, crossing.confidence)
        if not resolution.is_countable:
            return  # below the mapping's confidence threshold

        event_id = new_event_id()
        snapshot_path: str | None = None
        if self._snapshot_saver is not None:
            label = f"{crossing.class_name} {crossing.direction} {crossing.confidence:.2f}"
            try:
                snapshot_path = self._snapshot_saver.save(
                    frame,
                    event_id=event_id,
                    ts_ms=crossing.ts_ms,
                    bbox_px=crossing.bbox_px,
                    line_px=(counter.a, counter.b),
                    label=label,
                )
            except OSError:
                logger.exception("snapshot save failed for event %s", event_id)

        payload: dict[str, Any] = {
            "id": event_id,
            "restaurant_id": self.spec.restaurant_id,
            "camera_id": self.spec.camera_id,
            "line_id": crossing.line_id,
            "track_id": crossing.track_id,
            "category_id": resolution.category_id,
            "menu_item_id": resolution.menu_item_id,
            "raw_class_name": crossing.class_name,
            "confidence": crossing.confidence,
            "direction": DIRECTION_TO_DB[crossing.direction],
            "ts": crossing.ts_ms,
            "frame_index": crossing.frame_index,
            "anchor_x": crossing.anchor_norm[0],
            "anchor_y": crossing.anchor_norm[1],
            "snapshot_path": snapshot_path,
            # Extra context for live consumers (ignored by the DB writer):
            "canonical_direction": crossing.direction,
            "class_name": crossing.class_name,
        }
        result.add(crossing, payload)
        if self.bus is not None:
            self.bus.publish(TOPIC_CROSSING, payload)

    def _on_gap(self, from_mono: float, to_mono: float, reason: str) -> None:
        """VideoSource outage callback -> ``gap`` payload (epoch ms)."""
        self.result.gaps += 1
        mono_to_epoch_ms = time.time() * 1000 - time.monotonic() * 1000
        payload = {
            "restaurant_id": self.spec.restaurant_id,
            "camera_id": self.spec.camera_id,
            "from_ts": int(from_mono * 1000 + mono_to_epoch_ms),
            "to_ts": int(to_mono * 1000 + mono_to_epoch_ms),
            "reason": reason,
        }
        if self.bus is not None:
            self.bus.publish(TOPIC_GAP, payload)
