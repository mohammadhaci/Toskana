"""State-machine tests for :class:`LineCrossingCounter` with hand-fed tracks.

Geometry used throughout: 640x360 frame, vertical counting line drawn
top->bottom at x=320 (normalized ``0.5,0.0,0.5,1.0``), hysteresis band
+-12 px, ``min_track_age=3``, ``side_confirm_frames=2``, ``cooldown_ms=1500``.
Frames are fed at 100 ms steps. Positive = left -> right.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from toskana.vision.detector import TrackedDetection
from toskana.vision.line_crossing import (
    CrossingEvent,
    LineCrossingCounter,
    LineSpec,
)

BASE_SPEC = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, line_id=7)
TS_STEP_MS = 100

# One-track position scripts (x coordinates, y fixed): the object is 30 px
# wide, so the bottom-center anchor x equals the scripted x.
APPROACH_LEFT = [200.0, 260.0, 300.0]  # ages 1..3 -> ARMED(side=-1) at x=300
CROSS_RIGHT = [340.0, 360.0]  # two confirm frames beyond the +12 px band


def make_counter(**overrides: object) -> LineCrossingCounter:
    return LineCrossingCounter(replace(BASE_SPEC, **overrides), 640, 360)


def det(
    track_id: int,
    x: float,
    y: float = 180.0,
    age: int = 1,
    *,
    coasting: bool = False,
    class_id: int = 0,
    class_name: str = "drink",
) -> TrackedDetection:
    """A 30x30 tracked box whose bottom-center anchor is exactly ``(x, y)``."""
    return TrackedDetection(
        x1=x - 15.0,
        y1=y - 30.0,
        x2=x + 15.0,
        y2=y,
        class_id=class_id,
        class_name=class_name,
        confidence=0.95,
        track_id=track_id,
        coasting=coasting,
        track_age=age,
    )


def feed(counter: LineCrossingCounter, frames: list[list[TrackedDetection]]) -> list[CrossingEvent]:
    events: list[CrossingEvent] = []
    for index, dets in enumerate(frames):
        events.extend(counter.update(dets, ts_ms=index * TS_STEP_MS, frame_index=index))
    return events


def one_track(
    xs: list[float],
    *,
    y: float = 180.0,
    track_id: int = 1,
    coasting_frames: set[int] | None = None,
) -> list[list[TrackedDetection]]:
    coasting_frames = coasting_frames or set()
    return [
        [det(track_id, x, y, age=index + 1, coasting=index in coasting_frames)]
        for index, x in enumerate(xs)
    ]


class TestBasicCrossing:
    def test_positive_crossing_left_to_right(self) -> None:
        counter = make_counter()
        events = feed(counter, one_track(APPROACH_LEFT + CROSS_RIGHT + [400.0]))
        assert len(events) == 1
        (event,) = events
        assert event.direction == "positive"  # left -> right on a top->bottom line
        assert event.track_id == 1
        assert event.class_name == "drink"
        assert event.line_id == 7
        assert event.frame_index == 4  # second confirm frame
        assert event.anchor_px == (360.0, 180.0)
        assert event.anchor_norm == (360.0 / 640.0, 180.0 / 360.0)
        assert event.ts_ms == 4 * TS_STEP_MS

    def test_negative_crossing_right_to_left(self) -> None:
        counter = make_counter()
        events = feed(counter, one_track([440.0, 380.0, 340.0, 300.0, 280.0]))
        assert [e.direction for e in events] == ["negative"]

    def test_no_crossing_on_one_side(self) -> None:
        counter = make_counter()
        assert feed(counter, one_track([100.0, 150.0, 200.0, 250.0, 300.0])) == []


class TestMinTrackAge:
    def test_crossing_before_maturity_is_not_counted(self) -> None:
        # The track crosses while still TENTATIVE (age < 3): when it finally
        # arms, it is already on the right side -> no side change, no event.
        counter = make_counter()
        events = feed(counter, one_track([300.0, 340.0, 360.0, 380.0, 400.0]))
        assert events == []

    def test_same_path_counts_with_min_track_age_1(self) -> None:
        counter = make_counter(min_track_age=1)
        events = feed(counter, one_track([300.0, 340.0, 360.0, 380.0, 400.0]))
        assert [e.direction for e in events] == ["positive"]

    def test_track_born_inside_band_arms_only_on_a_clear_side(self) -> None:
        # Ages 1..3 are inside the hysteresis band -> not armed; the first
        # clear side is the right side, so no crossing is ever seen.
        counter = make_counter()
        events = feed(counter, one_track([318.0, 316.0, 319.0, 340.0, 360.0, 380.0]))
        assert events == []


class TestHysteresis:
    def test_loitering_inside_band_never_counts(self) -> None:
        counter = make_counter()
        wiggle = [315.0, 326.0, 313.0, 327.0, 314.0, 328.0, 312.0]  # all within +-12 px
        events = feed(counter, one_track(APPROACH_LEFT + wiggle + [300.0, 280.0]))
        assert events == []

    def test_loiter_then_decisive_crossing_counts_once(self) -> None:
        counter = make_counter()
        wiggle = [315.0, 326.0, 313.0, 327.0, 314.0]
        events = feed(counter, one_track(APPROACH_LEFT + wiggle + CROSS_RIGHT + [400.0]))
        assert [e.direction for e in events] == ["positive"]

    def test_hysteresis_scales_with_frame_width(self) -> None:
        # 12 px at 640 reference -> 24 px at 1280.
        assert make_counter().hysteresis_px == pytest.approx(12.0)
        wide = LineCrossingCounter(BASE_SPEC, 1280, 720)
        assert wide.hysteresis_px == pytest.approx(24.0)


class TestSideConfirmation:
    def test_single_frame_flicker_is_not_confirmed(self) -> None:
        counter = make_counter()
        flicker = [340.0, 300.0, 340.0, 300.0, 340.0, 300.0]  # never 2 in a row
        events = feed(counter, one_track(APPROACH_LEFT + flicker))
        assert events == []

    def test_neutral_frame_breaks_the_confirmation_streak(self) -> None:
        counter = make_counter()
        # 340 (candidate 1), 325 (neutral: streak reset), 340 (candidate 1),
        # 360 (candidate 2 -> confirmed).
        events = feed(counter, one_track(APPROACH_LEFT + [340.0, 325.0, 340.0, 360.0]))
        assert len(events) == 1
        assert events[0].frame_index == 6

    def test_longer_confirmation_window(self) -> None:
        counter = make_counter(side_confirm_frames=4)
        events = feed(counter, one_track(APPROACH_LEFT + [340.0, 350.0, 360.0]))
        assert events == []  # only 3 confirm frames
        counter = make_counter(side_confirm_frames=4)
        events = feed(counter, one_track(APPROACH_LEFT + [340.0, 350.0, 360.0, 370.0]))
        assert len(events) == 1


class TestCooldown:
    def test_rebounce_within_cooldown_is_rejected(self) -> None:
        counter = make_counter()
        xs = APPROACH_LEFT + CROSS_RIGHT  # emit at ts=400
        xs += [300.0, 280.0]  # back across, confirmed at ts=600 -> cooldown
        events = feed(counter, one_track(xs))
        assert [e.direction for e in events] == ["positive"]
        assert counter.stats.cooldown == 1

    def test_crossing_after_cooldown_expiry_counts(self) -> None:
        counter = make_counter()
        xs = APPROACH_LEFT + CROSS_RIGHT  # positive emitted at ts=400
        xs += [300.0, 280.0]  # negative at ts=600 -> rejected (cooldown)
        xs += [280.0] * 13  # wait on the left until ts=1900
        xs += CROSS_RIGHT  # confirmed at ts=2100: 2100-400 >= 1500
        events = feed(counter, one_track(xs))
        assert [e.direction for e in events] == ["positive", "positive"]
        assert counter.stats.cooldown == 1


class TestFiniteSegment:
    def test_path_beside_the_segment_does_not_count(self) -> None:
        # Line covers only the top half: (320, 0) -> (320, 180).
        counter = make_counter(y2=0.5)
        events = feed(counter, one_track(APPROACH_LEFT + CROSS_RIGHT + [400.0], y=300.0))
        assert events == []
        assert counter.stats.off_segment == 1

    def test_path_through_the_segment_counts(self) -> None:
        counter = make_counter(y2=0.5)
        events = feed(counter, one_track(APPROACH_LEFT + CROSS_RIGHT + [400.0], y=100.0))
        assert len(events) == 1


class TestCoasting:
    def test_coasting_across_the_line_emits_nothing(self) -> None:
        counter = make_counter()
        xs = APPROACH_LEFT + [340.0, 360.0, 380.0, 400.0]
        coasting = {3, 4, 5, 6}  # every frame past the line is coasted
        events = feed(counter, one_track(xs, coasting_frames=coasting))
        assert events == []

    def test_state_survives_coasting_and_counts_on_fresh_detections(self) -> None:
        counter = make_counter()
        xs = APPROACH_LEFT + [310.0, 315.0] + CROSS_RIGHT + [400.0]
        events = feed(counter, one_track(xs, coasting_frames={3, 4}))
        assert [e.direction for e in events] == ["positive"]

    def test_no_arming_while_coasting(self) -> None:
        counter = make_counter()
        xs = [200.0, 260.0, 300.0, 305.0] + CROSS_RIGHT + [400.0]
        # All left-side frames are coasted -> never armed on the left; the
        # first fresh frames are on the right -> arms right, no crossing.
        events = feed(counter, one_track(xs, coasting_frames={0, 1, 2, 3}))
        assert events == []


class TestDirectionFilter:
    def test_positive_only_ignores_returns(self) -> None:
        counter = make_counter(count_directions="positive")
        xs = APPROACH_LEFT + CROSS_RIGHT + [400.0] * 14  # positive at ts=400
        xs += [300.0, 280.0, 260.0]  # negative confirmed at ts >= 2100 (no cooldown)
        events = feed(counter, one_track(xs))
        assert [e.direction for e in events] == ["positive"]
        assert counter.stats.direction_filtered == 1

    def test_negative_only_ignores_outgoing(self) -> None:
        counter = make_counter(count_directions="negative")
        events = feed(counter, one_track(APPROACH_LEFT + CROSS_RIGHT + [400.0]))
        assert events == []
        assert counter.stats.direction_filtered == 1


class TestIdSwitchGuard:
    def test_id_switch_duplicate_is_suppressed(self) -> None:
        counter = make_counter()
        events: list[CrossingEvent] = []
        # Track 1 crosses first; track 2 (same class) appears later and
        # crosses on a nearly identical path (anchor 20 px away, 300 ms
        # later) — the tracker lost the identity, same physical object.
        track1_xs = [200.0, 260.0, 300.0, 340.0, 360.0]
        track2_xs = [200.0, 260.0, 300.0, 340.0, 360.0]
        for frame in range(8):
            dets = []
            if frame < len(track1_xs):
                dets.append(det(1, track1_xs[frame], 180.0, age=frame + 1))
            if 3 <= frame < 3 + len(track2_xs):  # track 2 born at frame 3
                dets.append(det(2, track2_xs[frame - 3], 200.0, age=frame - 2))
            events.extend(counter.update(dets, ts_ms=frame * TS_STEP_MS, frame_index=frame))
        assert len(events) == 1  # only track 1's crossing
        assert events[0].track_id == 1
        assert counter.stats.id_switch == 1

    def test_simultaneous_tray_items_are_not_suppressed(self) -> None:
        counter = make_counter()
        events: list[CrossingEvent] = []
        xs = APPROACH_LEFT + CROSS_RIGHT + [400.0]
        for frame, x in enumerate(xs):
            dets = [
                det(1, x, 180.0, age=frame + 1, class_name="main", class_id=1),
                det(2, x, 250.0, age=frame + 1, class_name="main", class_id=1),  # 70 px apart
            ]
            events.extend(counter.update(dets, ts_ms=frame * TS_STEP_MS, frame_index=frame))
        assert len(events) == 2
        assert {e.track_id for e in events} == {1, 2}
        assert counter.stats.id_switch == 0

    def test_different_class_is_not_suppressed(self) -> None:
        counter = make_counter()
        events: list[CrossingEvent] = []
        xs = APPROACH_LEFT + CROSS_RIGHT + [400.0]
        for frame, x in enumerate(xs):
            dets = [
                det(1, x, 180.0, age=frame + 1, class_name="drink", class_id=0),
                det(2, x, 200.0, age=frame + 1, class_name="main", class_id=1),  # 20 px apart
            ]
            events.extend(counter.update(dets, ts_ms=frame * TS_STEP_MS, frame_index=frame))
        assert len(events) == 2
        assert counter.stats.id_switch == 0

    def test_outside_time_window_is_not_suppressed(self) -> None:
        counter = make_counter(id_switch_window_ms=1200)
        events: list[CrossingEvent] = []
        track1_xs = [200.0, 260.0, 300.0, 340.0, 360.0]  # emits at ts=400
        for frame in range(25):
            dets = []
            if frame < len(track1_xs):
                dets.append(det(1, track1_xs[frame], 180.0, age=frame + 1))
            if 15 <= frame < 15 + len(track1_xs):  # track 2 born ts=1500; crosses ts=1900
                dets.append(det(2, track1_xs[frame - 15], 180.0, age=frame - 14))
            events.extend(counter.update(dets, ts_ms=frame * TS_STEP_MS, frame_index=frame))
        assert len(events) == 2  # 1900 - 400 = 1500 ms > 1200 ms window
        assert counter.stats.id_switch == 0


class TestLineSpecFromRow:
    def test_from_db_row_vocabulary_and_params(self) -> None:
        row = {
            "id": 42,
            "name": "Pass",
            "x1": 0.2,
            "y1": 0.1,
            "x2": 0.8,
            "y2": 0.9,
            "count_directions": "out,in",
            "min_track_age": 5,
            "hysteresis_px": 20,
            "cooldown_ms": 2000,
        }
        spec = LineSpec.from_row(row)
        assert spec.line_id == 42
        assert spec.count_directions == "both"
        assert spec.min_track_age == 5
        assert spec.hysteresis_px == 20.0
        assert spec.cooldown_ms == 2000
        assert spec.side_confirm_frames == 2  # default kept

    @pytest.mark.parametrize(
        ("db_value", "expected"),
        [("out,in", "both"), ("out", "positive"), ("in", "negative"), ("both", "both")],
    )
    def test_count_directions_vocabulary(self, db_value: str, expected: str) -> None:
        row = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0, "count_directions": db_value}
        assert LineSpec.from_row(row).count_directions == expected

    def test_unknown_count_directions_raises(self) -> None:
        row = {"x1": 0.5, "y1": 0.0, "x2": 0.5, "y2": 1.0, "count_directions": "sideways"}
        with pytest.raises(ValueError, match="count_directions"):
            LineSpec.from_row(row)

    def test_invalid_anchor_mode_raises(self) -> None:
        with pytest.raises(ValueError, match="anchor_mode"):
            LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, anchor_mode="top_left")
