"""Deterministic synthetic pass-through scenarios (specs + ground truth).

Each scenario function returns a fully deterministic :class:`ScenarioSpec`
(or :class:`TwoCamScenario`): scripted piecewise-linear object paths on a
640x360 @ 15 FPS canvas, plus the ground-truth crossings of the virtual
counting line. No randomness anywhere — the same call always produces the
same spec, and the renderer in ``make_synthetic_video`` draws it with
integer-deterministic OpenCV primitives.

Conventions
-----------
* The counting line is vertical at ``x = 0.5`` (normalized), i.e. 320 px.
* ``positive`` direction = left -> right (kitchen -> customers);
  ``negative`` = right -> left (returns).
* Color encodes class via :data:`COLOR_CLASS`
  (pure blue = drink, pure red = main, pure yellow = dessert).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field

from toskana.vision.backends.synthetic import COLOR_CLASS

__all__ = [
    "BACKGROUND_BGR",
    "COLOR_CLASS",
    "FPS",
    "FRAME_HEIGHT",
    "FRAME_WIDTH",
    "LINE_X_NORM",
    "LINE_X_PX",
    "NEGATIVE",
    "POSITIVE",
    "SCENARIOS",
    "Crossing",
    "ObjectSpec",
    "ScenarioSpec",
    "TwoCamScenario",
    "Waypoint",
]

FRAME_WIDTH = 640
FRAME_HEIGHT = 360
FPS = 15
LINE_X_NORM = 0.5
LINE_X_PX = LINE_X_NORM * FRAME_WIDTH  # 320.0
BACKGROUND_BGR = (24, 24, 24)  # plain dark background

POSITIVE = "positive"  # left -> right (kitchen -> customers)
NEGATIVE = "negative"  # right -> left (returns)


@dataclass(frozen=True)
class Waypoint:
    """Object center at a given frame; positions between waypoints are
    linearly interpolated."""

    frame: int
    x: float
    y: float


@dataclass(frozen=True)
class Crossing:
    """One ground-truth line crossing the counter must produce."""

    object_id: str
    class_key: str
    direction: str  # POSITIVE | NEGATIVE (physical/canonical direction)
    crossing_frame: int


@dataclass(frozen=True)
class ObjectSpec:
    """A scripted item: shape, class color and piecewise-linear path."""

    object_id: str
    class_key: str
    shape: str  # "circle" | "rect"
    size: int  # circle diameter / rect side, px
    waypoints: tuple[Waypoint, ...]
    hidden_frames: frozenset[int] = frozenset()  # simulated detection dropout

    def __post_init__(self) -> None:
        if self.class_key not in COLOR_CLASS:
            raise ValueError(f"unknown class_key: {self.class_key!r}")
        if self.shape not in ("circle", "rect"):
            raise ValueError(f"unknown shape: {self.shape!r}")
        frames = [wp.frame for wp in self.waypoints]
        if len(frames) < 2 or frames != sorted(set(frames)):
            raise ValueError("waypoints must be >= 2 with strictly increasing frames")

    @property
    def first_frame(self) -> int:
        return self.waypoints[0].frame

    @property
    def last_frame(self) -> int:
        return self.waypoints[-1].frame

    def center_at(self, frame: int) -> tuple[float, float] | None:
        """Interpolated center at ``frame``; None outside the path's span."""
        if frame < self.first_frame or frame > self.last_frame:
            return None
        for a, b in zip(self.waypoints, self.waypoints[1:], strict=False):
            if a.frame <= frame <= b.frame:
                t = (frame - a.frame) / (b.frame - a.frame)
                return (a.x + (b.x - a.x) * t, a.y + (b.y - a.y) * t)
        return None  # pragma: no cover - unreachable by construction

    def is_visible(self, frame: int) -> bool:
        return self.center_at(frame) is not None and frame not in self.hidden_frames


@dataclass(frozen=True)
class ScenarioSpec:
    """Everything needed to render one camera's video + ground truth."""

    name: str
    num_frames: int
    objects: tuple[ObjectSpec, ...]
    crossings: tuple[Crossing, ...]
    width: int = FRAME_WIDTH
    height: int = FRAME_HEIGHT
    fps: int = FPS
    line_x_norm: float = LINE_X_NORM
    mirrored: bool = False  # second-viewpoint camera rendered mirrored

    @property
    def line_x_px(self) -> float:
        return self.line_x_norm * self.width


@dataclass(frozen=True)
class TwoCamScenario:
    """Two viewpoints of the same physical crossings (dedup fixture)."""

    name: str
    cams: dict[str, ScenarioSpec] = field(default_factory=dict)
    canonical_crossings: tuple[Crossing, ...] = ()


def _linear_object(
    object_id: str,
    class_key: str,
    shape: str,
    size: int,
    *,
    y: float,
    x_from: float,
    x_to: float,
    frame_from: int,
    frame_to: int,
    hidden_frames: Sequence[int] = (),
) -> ObjectSpec:
    return ObjectSpec(
        object_id=object_id,
        class_key=class_key,
        shape=shape,
        size=size,
        waypoints=(Waypoint(frame_from, x_from, y), Waypoint(frame_to, x_to, y)),
        hidden_frames=frozenset(hidden_frames),
    )


def sign_change_crossings(obj: ObjectSpec, line_x: float = LINE_X_PX) -> list[Crossing]:
    """All naive center-side changes over the full path (incl. hidden frames).

    Scenario constructors pick the intended ground-truth crossings from
    these (e.g. loitering produces many raw sign changes but exactly one
    counted crossing).
    """
    crossings: list[Crossing] = []
    prev_side = 0
    for frame in range(obj.first_frame, obj.last_frame + 1):
        center = obj.center_at(frame)
        assert center is not None
        x = center[0]
        if x == line_x:
            raise ValueError(
                f"{obj.object_id}: center exactly on the line at frame {frame}; "
                "adjust the path to keep crossings unambiguous"
            )
        side = 1 if x > line_x else -1
        if prev_side != 0 and side != prev_side:
            direction = POSITIVE if side > 0 else NEGATIVE
            crossings.append(Crossing(obj.object_id, obj.class_key, direction, frame))
        prev_side = side
    return crossings


# --------------------------------------------------------------------------
# Scenarios
# --------------------------------------------------------------------------


def single_drink() -> ScenarioSpec:
    """One blue drink crossing once. GT: 1 positive crossing."""
    obj = _linear_object(
        "drink_1", "drink", "circle", 36, y=180, x_from=60, x_to=580, frame_from=0, frame_to=59
    )
    crossings = sign_change_crossings(obj)
    assert [c.direction for c in crossings] == [POSITIVE]
    return ScenarioSpec("single_drink", 60, (obj,), tuple(crossings))


def tray_carry_3() -> ScenarioSpec:
    """Three items carried as a rigid tray group crossing together.

    GT: 3 positive crossings in the same second (same frame, actually).
    """
    specs = (
        ("tray_drink", "drink", "circle", 36, 110.0),
        ("tray_main_1", "main", "rect", 44, 180.0),
        ("tray_main_2", "main", "rect", 44, 250.0),
    )
    objects = tuple(
        _linear_object(
            object_id, class_key, shape, size, y=y, x_from=60, x_to=580, frame_from=0, frame_to=59
        )
        for object_id, class_key, shape, size, y in specs
    )
    crossings: list[Crossing] = []
    for obj in objects:
        obj_crossings = sign_change_crossings(obj)
        assert [c.direction for c in obj_crossings] == [POSITIVE]
        crossings.extend(obj_crossings)
    assert len({c.crossing_frame for c in crossings}) == 1  # rigid group: same frame
    return ScenarioSpec("tray_carry_3", 60, objects, tuple(crossings))


def reverse_return() -> ScenarioSpec:
    """One main crosses out, pauses, comes back. GT: 1 positive + 1 negative."""
    obj = ObjectSpec(
        object_id="main_1",
        class_key="main",
        shape="rect",
        size=44,
        waypoints=(
            Waypoint(0, 80, 180),
            Waypoint(29, 420, 180),
            Waypoint(44, 420, 180),  # pause on the customer side
            Waypoint(74, 80, 180),
        ),
    )
    crossings = sign_change_crossings(obj)
    assert [c.direction for c in crossings] == [POSITIVE, NEGATIVE]
    return ScenarioSpec("reverse_return", 75, (obj,), tuple(crossings))


def loiter_on_line() -> ScenarioSpec:
    """One drink oscillates +-8 px around the line for ~2 s, then completes.

    GT: exactly 1 positive crossing (the final decisive one) — the raw path
    wiggles across the line many times, which hysteresis must absorb.
    """
    waypoints = [Waypoint(0, 60, 180), Waypoint(24, 312, 180)]
    x_cycle = (328.0, 312.0)  # +-8 px around 320
    for i, frame in enumerate(range(27, 55, 3)):  # frames 27..54, 2 s of wiggling
        waypoints.append(Waypoint(frame, x_cycle[i % 2], 180))
    assert waypoints[-1] == Waypoint(54, 312, 180)
    waypoints.append(Waypoint(74, 580, 180))
    obj = ObjectSpec(
        object_id="drink_1",
        class_key="drink",
        shape="circle",
        size=36,
        waypoints=tuple(waypoints),
    )
    raw = sign_change_crossings(obj)
    # Raw wiggles cross back and forth; the counted crossing is the last one.
    assert len(raw) > 3
    final = raw[-1]
    assert final.direction == POSITIVE
    positives = sum(1 for c in raw if c.direction == POSITIVE)
    negatives = sum(1 for c in raw if c.direction == NEGATIVE)
    assert positives == negatives + 1  # net effect: exactly one positive
    return ScenarioSpec("loiter_on_line", 75, (obj,), (final,))


def occlusion_gap() -> ScenarioSpec:
    """One main crossing; detections vanish for 5 frames before the line.

    GT: 1 positive crossing — the tracker must survive the dropout.
    """
    hidden = range(18, 23)  # 5 consecutive frames, well before the line
    obj = _linear_object(
        "main_1",
        "main",
        "rect",
        44,
        y=180,
        x_from=60,
        x_to=580,
        frame_from=0,
        frame_to=59,
        hidden_frames=hidden,
    )
    crossings = sign_change_crossings(obj)
    assert [c.direction for c in crossings] == [POSITIVE]
    for frame in hidden:  # the whole gap lies on the kitchen side
        center = obj.center_at(frame)
        assert center is not None and center[0] < LINE_X_PX
    assert max(hidden) < crossings[0].crossing_frame
    return ScenarioSpec("occlusion_gap", 60, (obj,), tuple(crossings))


_CAM_B_DELAY_FRAMES = 3  # ~200 ms at 15 FPS (second viewpoint latency)
_CAM_B_Y_SHIFT = 25.0


def _mirror_object(obj: ObjectSpec, width: int = FRAME_WIDTH) -> ObjectSpec:
    """Second-viewpoint transform: horizontal mirror + delay + y shift."""
    return ObjectSpec(
        object_id=obj.object_id,
        class_key=obj.class_key,
        shape=obj.shape,
        size=obj.size,
        waypoints=tuple(
            Waypoint(wp.frame + _CAM_B_DELAY_FRAMES, width - wp.x, wp.y + _CAM_B_Y_SHIFT)
            for wp in obj.waypoints
        ),
        hidden_frames=frozenset(f + _CAM_B_DELAY_FRAMES for f in obj.hidden_frames),
    )


def two_cam_same_exit() -> TwoCamScenario:
    """Two cameras watching the same exit: drink then main, 1 s apart.

    ``cam_b`` is mirrored horizontally and delayed ~200 ms (3 frames).
    GT: canonically 2 crossings — dedup must not double count.
    ``direction`` is always the canonical/physical direction; in cam_b's
    mirrored pixel space, positive appears as right-to-left motion.
    """
    cam_a_objects = (
        _linear_object(
            "drink_1", "drink", "circle", 36, y=150, x_from=60, x_to=585, frame_from=0, frame_to=44
        ),
        _linear_object(
            "main_1", "main", "rect", 44, y=210, x_from=60, x_to=585, frame_from=15, frame_to=59
        ),
    )
    canonical: list[Crossing] = []
    for obj in cam_a_objects:
        obj_crossings = sign_change_crossings(obj)
        assert [c.direction for c in obj_crossings] == [POSITIVE]
        canonical.extend(obj_crossings)
    assert (canonical[1].crossing_frame - canonical[0].crossing_frame) == FPS  # 1 s apart

    num_frames = 66  # covers cam_b's delayed tail
    cam_a = ScenarioSpec("two_cam_same_exit_cam_a", num_frames, cam_a_objects, tuple(canonical))
    cam_b_objects = tuple(_mirror_object(obj) for obj in cam_a_objects)
    cam_b_crossings = tuple(
        Crossing(c.object_id, c.class_key, c.direction, c.crossing_frame + _CAM_B_DELAY_FRAMES)
        for c in canonical
    )
    cam_b = ScenarioSpec(
        "two_cam_same_exit_cam_b", num_frames, cam_b_objects, cam_b_crossings, mirrored=True
    )
    return TwoCamScenario(
        name="two_cam_same_exit",
        cams={"cam_a": cam_a, "cam_b": cam_b},
        canonical_crossings=tuple(canonical),
    )


SCENARIOS: dict[str, Callable[[], ScenarioSpec | TwoCamScenario]] = {
    "single_drink": single_drink,
    "tray_carry_3": tray_carry_3,
    "reverse_return": reverse_return,
    "loiter_on_line": loiter_on_line,
    "occlusion_gap": occlusion_gap,
    "two_cam_same_exit": two_cam_same_exit,
}
