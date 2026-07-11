"""Property-based tests: random straight-line trajectories never double count.

A monotonic constant-speed trajectory that starts on one side of the line
and ends on the other produces **exactly one** crossing (in the correct
direction); a trajectory that stays on one side produces **zero**.
"""

from __future__ import annotations

from hypothesis import assume, given, settings
from hypothesis import strategies as st

from toskana.vision.detector import TrackedDetection
from toskana.vision.line_crossing import CrossingEvent, LineCrossingCounter, LineSpec

FRAME_W, FRAME_H = 640, 360
LINE_X = 320.0
SPEC = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0)  # vertical, full height
TS_STEP_MS = 66  # ~15 FPS


def run_trajectory(xs: list[float], y: float) -> tuple[list[CrossingEvent], LineCrossingCounter]:
    counter = LineCrossingCounter(SPEC, FRAME_W, FRAME_H)
    events: list[CrossingEvent] = []
    for index, x in enumerate(xs):
        det = TrackedDetection(
            x1=x - 15.0,
            y1=y - 30.0,
            x2=x + 15.0,
            y2=y,
            class_id=0,
            class_name="drink",
            confidence=0.9,
            track_id=1,
            coasting=False,
            track_age=index + 1,
        )
        events.extend(counter.update([det], ts_ms=index * TS_STEP_MS, frame_index=index))
    return events, counter


def interpolate(x_start: float, x_end: float, num_frames: int) -> list[float]:
    step = (x_end - x_start) / (num_frames - 1)
    return [x_start + step * i for i in range(num_frames)]


@settings(max_examples=200, deadline=None)
@given(
    x_start=st.integers(min_value=20, max_value=260),
    x_end=st.integers(min_value=380, max_value=620),
    y=st.integers(min_value=35, max_value=355),
    num_frames=st.integers(min_value=10, max_value=60),
    left_to_right=st.booleans(),
)
def test_one_full_pass_counts_exactly_once(
    x_start: int, x_end: int, y: int, num_frames: int, left_to_right: bool
) -> None:
    band = LineCrossingCounter(SPEC, FRAME_W, FRAME_H).hysteresis_px
    a, b = (float(x_start), float(x_end)) if left_to_right else (float(x_end), float(x_start))
    xs = interpolate(a, b, num_frames)

    # The trajectory must allow arming (min_track_age frames clearly on the
    # start side) and confirmation (side_confirm_frames clearly on the end
    # side); anything faster is an ID-switch-grade teleport, not a carry.
    def clear_of_band(x: float, side: float) -> bool:
        return (x - LINE_X) * side > band

    start_side = -1.0 if left_to_right else 1.0
    assume(all(clear_of_band(x, start_side) for x in xs[: SPEC.min_track_age]))
    assume(all(clear_of_band(x, -start_side) for x in xs[-SPEC.side_confirm_frames :]))

    events, _counter = run_trajectory(xs, float(y))
    assert len(events) == 1
    assert events[0].direction == ("positive" if left_to_right else "negative")


@settings(max_examples=200, deadline=None)
@given(
    x_a=st.integers(min_value=20, max_value=300),
    x_b=st.integers(min_value=20, max_value=300),
    y=st.integers(min_value=35, max_value=355),
    num_frames=st.integers(min_value=10, max_value=60),
    right_side=st.booleans(),
)
def test_trajectory_on_one_side_counts_zero(
    x_a: int, x_b: int, y: int, num_frames: int, right_side: bool
) -> None:
    offset = 320.0 if right_side else 0.0  # mirror the [20, 300] range to the right
    xs = interpolate(x_a + offset, x_b + offset, num_frames)
    events, counter = run_trajectory(xs, float(y))
    assert events == []
    assert counter.stats.id_switch == 0
    assert counter.stats.cooldown == 0
