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
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP, EventBus
from toskana.events.writer import new_event_id
from toskana.vision.capture import VideoSource
from toskana.vision.detector import TrackingBackend
from toskana.vision.line_crossing import CrossingEvent, LineCrossingCounter, LineSpec
from toskana.vision.mapping import ClassMappingResolver, MappingRule, MenuItemInfo
from toskana.vision.snapshots import SnapshotSaver

logger = logging.getLogger(__name__)

#: counter direction -> ``events.direction`` column value.
DIRECTION_TO_DB = {"positive": "out", "negative": "in"}

#: Per-frame observer hook: ``(frame_index, ts_ms, frame_bgr, tracked)``.
#: Called after counters were updated for the frame (M4: live preview/stats).
FrameHook = Callable[[int, int, Any, list[Any]], None]


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
    #: Menu-item lookup (Phase 2): lets the resolver derive an item mapping's
    #: category (fallback chain) and attach the display name to payloads.
    menu_items: tuple[MenuItemInfo, ...] = ()
    snapshots_dir: str | None = None
    #: Performance preset knobs (see :mod:`toskana.vision.presets`):
    #: process every ``frame_skip``-th frame (1 = every frame) and run the
    #: YOLO backend at ``imgsz`` inference resolution.
    frame_skip: int = 1
    imgsz: int = 640

    def __post_init__(self) -> None:
        if self.frame_skip < 1:
            raise ValueError(f"frame_skip must be >= 1: {self.frame_skip}")


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
    #: Frames dropped by the ``frame_skip`` performance preset (not processed).
    frames_skipped: int = 0

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

        return YoloBackend(spec.model_path, device=spec.device, imgsz=spec.imgsz)
    raise ValueError(f"unknown backend: {spec.backend!r}")


class CameraPipeline:
    """Counts line crossings on one video source, headless."""

    def __init__(
        self,
        spec: PipelineSpec,
        *,
        bus: EventBus | None = None,
        tracking_backend: TrackingBackend | None = None,
        on_frame: FrameHook | None = None,
    ) -> None:
        self.spec = spec
        self.bus = bus
        self._backend = tracking_backend
        self._on_frame = on_frame
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
        resolver = ClassMappingResolver(spec.mapping_rules, menu_items=spec.menu_items)
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
                if spec.frame_skip > 1 and frame_index % spec.frame_skip != 0:
                    result.frames_skipped += 1
                    continue  # performance preset: process every Nth frame
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
                if self._on_frame is not None:
                    try:
                        self._on_frame(frame_index, ts_ms, frame, list(tracked))
                    except Exception:  # noqa: BLE001 - observer must not kill the pipeline
                        logger.exception("on_frame hook failed (camera %s)", spec.camera_id)
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
            "menu_item_name": resolution.menu_item_name,
            # Pixel bbox of the item — lets the RefinerEngine crop the
            # snapshot to the item before sending it to the vision LLM.
            "bbox_px": crossing.bbox_px,
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
