"""RELEASE GATE: exact end-to-end counting on every synthetic scenario.

generator -> encoded video -> VideoSource -> SyntheticShapeDetector ->
IouTracker -> LineCrossingCounter -> (bus -> EventWriter -> SQLite), asserted
EXACTLY against each scenario's ``ground_truth.json``.
"""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.tools.make_synthetic_video import GeneratedScenario, generate_scenario
from toskana.db.base import Base, make_engine
from toskana.db.models import Camera, Category, Event, Restaurant
from toskana.events.bus import EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory
from toskana.vision.backends.synthetic import SyntheticShapeDetector
from toskana.vision.detector import TrackedDetection, TrackingBackend
from toskana.vision.line_crossing import LineSpec
from toskana.vision.mapping import MappingRule
from toskana.vision.pipeline import CameraPipeline, PipelineResult, PipelineSpec
from toskana.vision.tracker import IouTracker

#: Scenario line: vertical at x=0.5, drawn top->bottom => positive = left->right.
LINE = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, line_id=None)

FRAME_TOLERANCE = 2  # confirmation lag allowed vs the naive GT crossing frame


def run_scenario(
    name: str,
    out_dir: Path,
    *,
    tracking_backend: TrackingBackend | None = None,
    bus: EventBus | None = None,
    mapping_rules: tuple[MappingRule, ...] = (),
    snapshots_dir: str | None = None,
) -> tuple[GeneratedScenario, PipelineResult, CameraPipeline]:
    generated = generate_scenario(name, out_dir)
    spec = PipelineSpec(
        restaurant_id=1,
        camera_id=1,
        source=str(generated.video_paths["cam"]),
        restaurant_slug="testaurant",
        backend="synthetic",
        paced=False,
        lines=(LINE,),
        mapping_rules=mapping_rules,
        snapshots_dir=snapshots_dir,
    )
    pipeline = CameraPipeline(spec, bus=bus, tracking_backend=tracking_backend)
    result = pipeline.run_once()
    assert result.frames == generated.ground_truth["video"]["num_frames"]
    return generated, result, pipeline


def counted(result: PipelineResult) -> Counter[tuple[str, str]]:
    """Multiset of (class_name, direction) over all emitted crossings."""
    return Counter((c.class_name, c.direction) for c in result.crossings)


def expected(generated: GeneratedScenario) -> Counter[tuple[str, str]]:
    """The same multiset straight from ground_truth.json."""
    return Counter((c["class_key"], c["direction"]) for c in generated.ground_truth["crossings"])


class TestScenariosExactCounts:
    def test_single_drink(self, tmp_path: Path) -> None:
        generated, result, _ = run_scenario("single_drink", tmp_path)
        assert counted(result) == expected(generated) == Counter({("drink", "positive"): 1})
        (event,) = result.crossings
        (gt,) = generated.ground_truth["crossings"]
        assert abs(event.frame_index - gt["crossing_frame"]) <= FRAME_TOLERANCE

    def test_tray_carry_3(self, tmp_path: Path) -> None:
        """Release gate: 3 items on one tray = exactly 3 events."""
        generated, result, pipeline = run_scenario("tray_carry_3", tmp_path)
        assert counted(result) == expected(generated)
        assert counted(result) == Counter({("drink", "positive"): 1, ("main", "positive"): 2})
        timestamps = [c.ts_ms for c in result.crossings]
        assert max(timestamps) - min(timestamps) < 1000  # within the same second
        (counter,) = pipeline.counters
        assert counter.stats.id_switch == 0  # distinct anchors: nothing suppressed

    def test_reverse_return(self, tmp_path: Path) -> None:
        generated, result, _ = run_scenario("reverse_return", tmp_path)
        assert counted(result) == expected(generated)
        assert counted(result) == Counter({("main", "positive"): 1, ("main", "negative"): 1})
        directions = [c.direction for c in result.crossings]
        assert directions == ["positive", "negative"]  # out first, then the return

    def test_loiter_on_line(self, tmp_path: Path) -> None:
        generated, result, _ = run_scenario("loiter_on_line", tmp_path)
        assert counted(result) == expected(generated) == Counter({("drink", "positive"): 1})
        assert result.total == 1  # the wiggling adds nothing

    def test_occlusion_gap(self, tmp_path: Path) -> None:
        generated, result, _ = run_scenario("occlusion_gap", tmp_path)
        assert counted(result) == expected(generated) == Counter({("main", "positive"): 1})


class _IdRecordingTracker:
    """Delegating wrapper that records every track id it ever returned."""

    def __init__(self, inner: TrackingBackend) -> None:
        self.inner = inner
        self.seen_ids: set[int] = set()

    @property
    def class_names(self) -> list[str]:
        return self.inner.class_names

    def detect_and_track(self, frame: np.ndarray) -> list[TrackedDetection]:
        tracked = self.inner.detect_and_track(frame)
        self.seen_ids.update(t.track_id for t in tracked)
        return tracked


class _GhostDuplicatingTracker:
    """Emits a duplicate of every fresh track under a different id (+1000),
    shifted 6 px — the classic double-detection / ID-switch failure mode."""

    def __init__(self, inner: TrackingBackend) -> None:
        self.inner = inner

    @property
    def class_names(self) -> list[str]:
        return self.inner.class_names

    def detect_and_track(self, frame: np.ndarray) -> list[TrackedDetection]:
        tracked = self.inner.detect_and_track(frame)
        ghosts = [
            TrackedDetection(
                x1=t.x1 + 6.0,
                y1=t.y1,
                x2=t.x2 + 6.0,
                y2=t.y2,
                class_id=t.class_id,
                class_name=t.class_name,
                confidence=t.confidence,
                track_id=t.track_id + 1000,
                coasting=t.coasting,
                track_age=t.track_age,
            )
            for t in tracked
            if not t.coasting
        ]
        return tracked + ghosts


class TestIdSwitchRobustness:
    def test_occlusion_with_forced_id_switch_still_counts_once(self, tmp_path: Path) -> None:
        """max_coast_frames=2 kills the track inside the 5-frame dropout, so a
        fresh id appears after the gap — the count must still be exactly 1."""
        backend = _IdRecordingTracker(IouTracker(SyntheticShapeDetector(), max_coast_frames=2))
        generated, result, _ = run_scenario("occlusion_gap", tmp_path, tracking_backend=backend)
        assert len(backend.seen_ids) >= 2  # the identity really did switch
        assert counted(result) == expected(generated) == Counter({("main", "positive"): 1})

    def test_ghost_duplicate_track_suppressed_by_id_switch_guard(self, tmp_path: Path) -> None:
        """A second track id riding the same object crosses simultaneously at
        (nearly) the same anchor: the ID-switch guard must swallow it."""
        backend = _GhostDuplicatingTracker(IouTracker(SyntheticShapeDetector()))
        generated, result, pipeline = run_scenario(
            "single_drink", tmp_path, tracking_backend=backend
        )
        assert counted(result) == expected(generated) == Counter({("drink", "positive"): 1})
        (counter,) = pipeline.counters
        assert counter.stats.id_switch >= 1  # the ghost was suppressed, not missed


class TestPersistence:
    def test_single_drink_persists_event_and_snapshot(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "toskana.db")
        engine = make_engine(db_path)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Restaurant(id=1, slug="testaurant", name="Testaurant"))
            session.flush()
            session.add(
                Camera(
                    id=1,
                    restaurant_id=1,
                    name="cam",
                    source_type="file",
                    source_url="scenario",
                )
            )
            session.add(
                Category(id=5, restaurant_id=1, key="drink", name_de="Getränk", name_en="Drink")
            )
            session.commit()
        engine.dispose()

        rules = (
            MappingRule(
                model_class_id=0, model_class_name="drink", category_id=5, min_confidence=0.5
            ),
        )
        snapshots_dir = tmp_path / "snapshots"
        bus = EventBus()
        factory = make_writer_session_factory(db_path)
        with EventWriter(factory, bus=bus) as writer:
            generated, result, _ = run_scenario(
                "single_drink",
                tmp_path,
                bus=bus,
                mapping_rules=rules,
                snapshots_dir=str(snapshots_dir),
            )
        assert result.total == 1
        assert writer.written_events == 1

        with factory() as session:
            (row,) = session.scalars(select(Event)).all()
        (gt,) = generated.ground_truth["crossings"]
        assert len(row.id) == 26  # ULID
        assert row.restaurant_id == 1
        assert row.camera_id == 1
        assert row.track_id == result.crossings[0].track_id
        assert row.category_id == 5  # resolved through the mapping
        assert row.menu_item_id is None
        assert row.raw_class_name == "drink"
        assert row.direction == "out"  # positive = kitchen -> customers
        assert row.confidence > 0.5
        assert row.frame_index is not None
        assert abs(row.frame_index - gt["crossing_frame"]) <= FRAME_TOLERANCE
        assert 0.0 < row.anchor_x < 1.0 and 0.0 < row.anchor_y < 1.0
        assert row.is_canonical is True

        assert row.snapshot_path is not None
        snapshot_file = snapshots_dir / row.snapshot_path
        assert snapshot_file.is_file() and snapshot_file.stat().st_size > 0
        assert row.snapshot_path.startswith("testaurant/")
        assert row.snapshot_path.endswith(f"{row.id}.jpg")

    def test_unmapped_class_still_persists_with_null_category(self, tmp_path: Path) -> None:
        db_path = str(tmp_path / "toskana.db")
        engine = make_engine(db_path)
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Restaurant(id=1, slug="testaurant", name="Testaurant"))
            session.flush()
            session.add(
                Camera(id=1, restaurant_id=1, name="cam", source_type="file", source_url="scenario")
            )
            session.commit()
        engine.dispose()

        bus = EventBus()
        factory = make_writer_session_factory(db_path)
        with EventWriter(factory, bus=bus) as writer:
            _generated, result, _ = run_scenario("single_drink", tmp_path, bus=bus)
        assert result.total == 1
        assert writer.written_events == 1
        with factory() as session:
            (row,) = session.scalars(select(Event)).all()
        assert row.category_id is None  # unmapped, but never silently dropped
        assert row.raw_class_name == "drink"


def _payload_sanity(payload: dict[str, Any]) -> None:
    assert payload["direction"] in ("out", "in")
    assert payload["canonical_direction"] in ("positive", "negative")
    assert 0.0 <= payload["anchor_x"] <= 1.0
    assert 0.0 <= payload["anchor_y"] <= 1.0


def test_bus_payloads_match_result(tmp_path: Path) -> None:
    bus = EventBus()
    received: list[dict[str, Any]] = []
    bus.subscribe("crossing", received.append)
    _generated, result, _ = run_scenario("tray_carry_3", tmp_path, bus=bus)
    assert len(received) == result.total == 3
    for payload in received:
        _payload_sanity(payload)
    assert received == result.payloads
