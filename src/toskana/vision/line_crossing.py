"""Line-crossing state machine — the counting core.

Per ``(track, line)`` state machine::

    NEW -> TENTATIVE (until track_age >= min_track_age)
        -> ARMED(stable_side)  (first frame outside the hysteresis band)
        -> confirmed side change -> CrossingEvent (subject to gates below)

Gates applied to a confirmed side change, in order:

1. **Finite segment** — the movement segment (last confirmed anchor ->
   current anchor) must intersect the finite line segment ``a -> b``;
   walking around the end of the counter does not count.
2. **Cooldown** — a crossing within ``cooldown_ms`` of the track's previous
   counted crossing on this line is rejected (anti-bounce).
3. **Direction filter** — ``count_directions``: ``both``/``positive``/``negative``.
4. **ID-switch guard** — a ring buffer of recently *emitted* crossings per
   line; a new crossing with the same direction + class whose anchor lies
   within ``id_switch_radius_px`` of a recent one (within
   ``id_switch_window_ms``) from a *different* track id is suppressed as a
   tracker identity switch. Simultaneous tray items pass because their
   anchors are spatially distinct.

Other behaviour:

* **Hysteresis band**: anchors closer than ``hysteresis_px`` (defined at a
  640 px reference width, scaled linearly with the actual frame width) to
  the line are *neutral* — no side updates, so an item loitering on the
  counter never bounces the counter.
* **Side-change confirmation**: the candidate side must persist for
  ``side_confirm_frames`` consecutive frames *beyond* the hysteresis band.
* **Coasting** detections freeze their state entirely (no side updates, no
  emission) but never reset it.

Direction semantics: ``positive`` = crossing from side ``-1`` to side ``+1``
of the directed line ``a -> b`` (see :mod:`toskana.vision.geometry`). For a
vertical line drawn top->bottom this means **left -> right**, matching the
synthetic scenarios' ground truth.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from toskana.vision.detector import TrackedDetection
from toskana.vision.geometry import (
    ANCHOR_MODES,
    Point,
    anchor_point,
    denormalize_line,
    perpendicular_distance_px,
    segments_intersect,
    side_of_line,
)

HYSTERESIS_REFERENCE_WIDTH = 640.0

Direction = Literal["positive", "negative"]
CountDirections = Literal["both", "positive", "negative"]

#: DB ``lines.count_directions`` vocabulary -> counter vocabulary.
#: ``out`` = kitchen -> customers = positive; ``in`` = returns = negative.
_DB_DIRECTIONS: dict[str, CountDirections] = {
    "out,in": "both",
    "in,out": "both",
    "both": "both",
    "out": "positive",
    "positive": "positive",
    "in": "negative",
    "negative": "negative",
}


def parse_count_directions(value: str) -> CountDirections:
    """Translate the DB ``count_directions`` vocabulary; raises on unknown."""
    normalized = "".join(value.split()).lower()
    try:
        return _DB_DIRECTIONS[normalized]
    except KeyError:
        raise ValueError(f"unknown count_directions: {value!r}") from None


#: Backwards-compatible private alias (pre-M4 name).
_parse_count_directions = parse_count_directions


@dataclass(frozen=True)
class LineSpec:
    """All tuning parameters of one counting line (decoupled from the ORM)."""

    x1: float  # normalized 0..1
    y1: float
    x2: float
    y2: float
    line_id: int | None = None
    name: str = "Line"
    count_directions: CountDirections = "both"
    min_track_age: int = 3
    hysteresis_px: float = 12.0  # at HYSTERESIS_REFERENCE_WIDTH
    cooldown_ms: int = 1500
    side_confirm_frames: int = 2
    anchor_mode: str = "bottom_center"
    id_switch_radius_px: float = 60.0
    id_switch_window_ms: int = 1200

    def __post_init__(self) -> None:
        if self.anchor_mode not in ANCHOR_MODES:
            raise ValueError(f"anchor_mode must be one of {ANCHOR_MODES}: {self.anchor_mode!r}")

    @classmethod
    def from_row(cls, row: Mapping[str, Any] | Any) -> LineSpec:
        """Build a spec from a DB ``lines`` row (dict-like or ORM object)."""
        if isinstance(row, LineSpec):
            return row
        if isinstance(row, Mapping):
            get: Any = row.get
        else:

            def get(key: str, default: Any = None) -> Any:
                return getattr(row, key, default)

        return cls(
            x1=float(get("x1")),
            y1=float(get("y1")),
            x2=float(get("x2")),
            y2=float(get("y2")),
            line_id=get("id"),
            name=str(get("name", "Line")),
            count_directions=_parse_count_directions(str(get("count_directions", "both"))),
            min_track_age=int(get("min_track_age", 3)),
            hysteresis_px=float(get("hysteresis_px", 12.0)),
            cooldown_ms=int(get("cooldown_ms", 1500)),
            side_confirm_frames=int(get("side_confirm_frames", 2)),
            anchor_mode=str(get("anchor_mode", "bottom_center")),
            id_switch_radius_px=float(get("id_switch_radius_px", 60.0)),
            id_switch_window_ms=int(get("id_switch_window_ms", 1200)),
        )


@dataclass(frozen=True)
class CrossingEvent:
    """One counted line crossing."""

    track_id: int
    direction: Direction
    class_id: int
    class_name: str
    confidence: float
    ts_ms: int
    frame_index: int
    anchor_px: Point
    anchor_norm: Point
    bbox_px: tuple[float, float, float, float]
    line_id: int | None = None


@dataclass
class _TrackState:
    armed: bool = False
    stable_side: int = 0
    candidate_side: int = 0
    candidate_frames: int = 0
    last_confirmed_anchor: Point | None = None
    last_cross_ts_ms: int | None = None
    last_seen_frame: int = 0


@dataclass(frozen=True)
class _EmittedCrossing:
    ts_ms: int
    direction: Direction
    class_name: str
    anchor_px: Point
    track_id: int


@dataclass
class SuppressionStats:
    """Counters for crossings rejected by each gate (exposed for tests/tuning)."""

    id_switch: int = 0
    cooldown: int = 0
    off_segment: int = 0
    direction_filtered: int = 0


class LineCrossingCounter:
    """State machine counting confirmed crossings of one line."""

    #: Track states idle longer than this many frames are garbage-collected.
    _STATE_TTL_FRAMES = 300

    def __init__(self, spec: LineSpec, frame_width: int, frame_height: int) -> None:
        if frame_width <= 0 or frame_height <= 0:
            raise ValueError(f"invalid frame size: {frame_width}x{frame_height}")
        self.spec = spec
        self.frame_width = frame_width
        self.frame_height = frame_height
        self.a, self.b = denormalize_line(
            (spec.x1, spec.y1, spec.x2, spec.y2), frame_width, frame_height
        )
        #: Hysteresis half-band in actual pixels, scaled from the 640 px reference.
        self.hysteresis_px = spec.hysteresis_px * frame_width / HYSTERESIS_REFERENCE_WIDTH
        self.stats = SuppressionStats()
        self._states: dict[int, _TrackState] = {}
        self._recent: deque[_EmittedCrossing] = deque(maxlen=64)

    # -- public API ----------------------------------------------------------

    def update(
        self,
        tracked: Iterable[TrackedDetection],
        *,
        ts_ms: int,
        frame_index: int,
    ) -> list[CrossingEvent]:
        """Advance one frame; returns the crossings counted in this frame."""
        events: list[CrossingEvent] = []
        for det in sorted(tracked, key=lambda t: t.track_id):
            event = self._update_track(det, ts_ms=ts_ms, frame_index=frame_index)
            if event is not None:
                events.append(event)
        self._prune(frame_index)
        return events

    # -- internals -----------------------------------------------------------

    def _update_track(
        self, det: TrackedDetection, *, ts_ms: int, frame_index: int
    ) -> CrossingEvent | None:
        state = self._states.get(det.track_id)
        if state is None:
            state = _TrackState()
            self._states[det.track_id] = state
        state.last_seen_frame = frame_index

        if det.coasting:
            return None  # freeze: no side updates, no emission, no reset

        anchor = anchor_point(det.bbox, self.spec.anchor_mode)
        distance = perpendicular_distance_px(anchor, self.a, self.b)
        in_band = distance < self.hysteresis_px
        side = 0 if in_band else side_of_line(anchor, self.a, self.b)

        if not state.armed:
            # TENTATIVE: wait for track maturity and a clear side.
            if det.track_age >= self.spec.min_track_age and side != 0:
                state.armed = True
                state.stable_side = side
                state.last_confirmed_anchor = anchor
            return None

        if side == 0:
            # Neutral zone: no side update; confirmation streak is broken.
            state.candidate_side = 0
            state.candidate_frames = 0
            return None

        if side == state.stable_side:
            state.candidate_side = 0
            state.candidate_frames = 0
            state.last_confirmed_anchor = anchor
            return None

        # Opposite side, beyond the band: build up confirmation.
        if state.candidate_side == side:
            state.candidate_frames += 1
        else:
            state.candidate_side = side
            state.candidate_frames = 1
        if state.candidate_frames < self.spec.side_confirm_frames:
            return None

        # Confirmed side change.
        previous_anchor = state.last_confirmed_anchor
        state.stable_side = side
        state.candidate_side = 0
        state.candidate_frames = 0
        state.last_confirmed_anchor = anchor

        # Gate 1: the movement must cross the FINITE line segment.
        if previous_anchor is None or not segments_intersect(
            previous_anchor, anchor, self.a, self.b
        ):
            self.stats.off_segment += 1
            return None

        # Gate 2: per-track cooldown.
        if (
            state.last_cross_ts_ms is not None
            and ts_ms - state.last_cross_ts_ms < self.spec.cooldown_ms
        ):
            self.stats.cooldown += 1
            return None
        state.last_cross_ts_ms = ts_ms

        direction: Direction = "positive" if side > 0 else "negative"

        # Gate 3: direction filter.
        if self.spec.count_directions not in ("both", direction):
            self.stats.direction_filtered += 1
            return None

        # Gate 4: ID-switch guard.
        if self._is_id_switch_duplicate(det, direction, anchor, ts_ms):
            self.stats.id_switch += 1
            return None

        event = CrossingEvent(
            track_id=det.track_id,
            direction=direction,
            class_id=det.class_id,
            class_name=det.class_name,
            confidence=det.confidence,
            ts_ms=ts_ms,
            frame_index=frame_index,
            anchor_px=anchor,
            anchor_norm=(anchor[0] / self.frame_width, anchor[1] / self.frame_height),
            bbox_px=det.bbox,
            line_id=self.spec.line_id,
        )
        self._recent.append(
            _EmittedCrossing(
                ts_ms=ts_ms,
                direction=direction,
                class_name=det.class_name,
                anchor_px=anchor,
                track_id=det.track_id,
            )
        )
        return event

    def _is_id_switch_duplicate(
        self, det: TrackedDetection, direction: Direction, anchor: Point, ts_ms: int
    ) -> bool:
        """A same-direction, same-class crossing from a *different* track id
        near a recently emitted one is a tracker identity switch."""
        for recent in self._recent:
            if recent.track_id == det.track_id:
                continue
            if recent.direction != direction or recent.class_name != det.class_name:
                continue
            if ts_ms - recent.ts_ms > self.spec.id_switch_window_ms:
                continue
            if math.dist(recent.anchor_px, anchor) < self.spec.id_switch_radius_px:
                return True
        return False

    def _prune(self, frame_index: int) -> None:
        stale = [
            track_id
            for track_id, state in self._states.items()
            if frame_index - state.last_seen_frame > self._STATE_TTL_FRAMES
        ]
        for track_id in stale:
            del self._states[track_id]
