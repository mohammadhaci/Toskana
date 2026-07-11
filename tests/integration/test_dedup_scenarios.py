"""M8 ACCEPTANCE: cross-camera dedup on the ``two_cam_same_exit`` scenario.

Two CameraPipelines (cam_a + the mirrored, ~200 ms delayed cam_b) run
concurrently against one EventBus with the EventWriter and the DedupEngine
attached, persisting into a tmp SQLite DB. Every raw event is kept (4 rows),
but the canonical count must equal the ground truth (2), never its double.

cam_b is a mirrored viewpoint, so its counting line is drawn bottom->top:
the physical kitchen->customers direction maps to ``out`` on both cameras.
"""

from __future__ import annotations

import time
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from tests.tools.make_synthetic_video import GeneratedScenario, generate_scenario
from toskana.db.base import Base, make_engine
from toskana.db.models import Camera, Category, Event, ExitGroup, Restaurant
from toskana.events.bus import TOPIC_CROSSING, EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory, new_event_id
from toskana.vision.dedup import DedupEngine, load_camera_groups
from toskana.vision.line_crossing import LineSpec
from toskana.vision.mapping import MappingRule
from toskana.vision.pipeline import CameraPipeline, PipelineSpec

RESTAURANT_ID = 1
CAM_A_ID = 1  # primary in the exit group
CAM_B_ID = 2
CAT_DRINK = 10
CAT_MAIN = 20

#: cam_a sees kitchen on the left: top->bottom line, positive = left->right.
LINE_A = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, line_id=None)
#: cam_b is mirrored: bottom->top line so the same physical motion is positive.
LINE_B = LineSpec(x1=0.5, y1=1.0, x2=0.5, y2=0.0, line_id=None)

MAPPING_RULES = (
    MappingRule(model_class_id=0, model_class_name="drink", category_id=CAT_DRINK),
    MappingRule(model_class_id=1, model_class_name="main", category_id=CAT_MAIN),
)


def _seed_db(db_path: str, *, dedup_strategy: str) -> None:
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Restaurant(id=RESTAURANT_ID, slug="testaurant", name="Testaurant"))
        session.flush()
        session.add(
            ExitGroup(
                id=1,
                restaurant_id=RESTAURANT_ID,
                name="Pass",
                dedup_window_ms=2000,
                dedup_strategy=dedup_strategy,
            )
        )
        session.flush()
        session.add_all(
            [
                Camera(
                    id=CAM_A_ID,
                    restaurant_id=RESTAURANT_ID,
                    name="cam_a",
                    source_type="file",
                    source_url="cam_a",
                    exit_group_id=1,
                    is_primary_in_group=True,
                ),
                Camera(
                    id=CAM_B_ID,
                    restaurant_id=RESTAURANT_ID,
                    name="cam_b",
                    source_type="file",
                    source_url="cam_b",
                    exit_group_id=1,
                ),
                Category(
                    id=CAT_DRINK,
                    restaurant_id=RESTAURANT_ID,
                    key="drink",
                    name_de="Getränk",
                    name_en="Drink",
                ),
                Category(
                    id=CAT_MAIN,
                    restaurant_id=RESTAURANT_ID,
                    key="main",
                    name_de="Haupt",
                    name_en="Main",
                ),
            ]
        )
        session.commit()
    engine.dispose()


def _pipeline_spec(camera_id: int, video: Path, line: LineSpec) -> PipelineSpec:
    return PipelineSpec(
        restaurant_id=RESTAURANT_ID,
        camera_id=camera_id,
        source=str(video),
        restaurant_slug="testaurant",
        backend="synthetic",
        paced=False,
        lines=(line,),
        mapping_rules=MAPPING_RULES,
    )


def _run_two_cam(
    tmp_path: Path, *, strategy: str
) -> tuple[GeneratedScenario, DedupEngine, EventWriter, sessionmaker[Session]]:
    generated = generate_scenario("two_cam_same_exit", tmp_path / "scenario")
    db_path = str(tmp_path / f"toskana-{strategy}.db")
    _seed_db(db_path, dedup_strategy=strategy)

    bus = EventBus()
    factory = make_writer_session_factory(db_path)
    with factory() as session:
        camera_groups = load_camera_groups(session, RESTAURANT_ID)
    assert set(camera_groups) == {CAM_A_ID, CAM_B_ID}
    assert camera_groups[CAM_A_ID].strategy == strategy
    assert camera_groups[CAM_A_ID].primary_camera_id == CAM_A_ID

    dedup = DedupEngine(camera_groups, bus=bus)
    pipelines = [
        CameraPipeline(_pipeline_spec(CAM_A_ID, generated.video_paths["cam_a"], LINE_A), bus=bus),
        CameraPipeline(_pipeline_spec(CAM_B_ID, generated.video_paths["cam_b"], LINE_B), bus=bus),
    ]
    with EventWriter(factory, bus=bus) as writer:
        dedup.start()  # after writer.start(): inserts queue before demotions
        try:
            for pipeline in pipelines:
                pipeline.start()
            for pipeline in pipelines:
                pipeline.join(timeout=120)
        finally:
            dedup.stop()
    # writer context exit == graceful stop: queue fully drained and flushed.
    for pipeline in pipelines:
        assert pipeline.result.total == 2  # each camera saw both raw crossings
    return generated, dedup, writer, factory


class TestTwoCamSameExit:
    def test_primary_wins_counts_ground_truth_not_double(self, tmp_path: Path) -> None:
        generated, dedup, writer, factory = _run_two_cam(tmp_path, strategy="primary_wins")
        gt_canonical = generated.ground_truth["canonical_crossings"]
        assert len(gt_canonical) == 2

        with factory() as session:
            events = session.scalars(select(Event).order_by(Event.ts, Event.id)).all()

        assert len(events) == 4  # every raw event is kept, never deleted
        canonical = [e for e in events if e.is_canonical]
        suppressed = [e for e in events if not e.is_canonical]
        assert len(canonical) == len(gt_canonical) == 2  # == GT, not its double
        assert sorted(e.category_id for e in canonical) == [CAT_DRINK, CAT_MAIN]
        assert all(e.direction == "out" for e in events)

        # primary_wins: every canonical event comes from the primary camera.
        assert {e.camera_id for e in canonical} == {CAM_A_ID}
        assert {e.camera_id for e in suppressed} == {CAM_B_ID}

        # Each suppressed event shares its dedup_group_id with its canonical
        # partner of the same category (one-to-one pairing).
        canonical_by_group = {e.dedup_group_id: e for e in canonical}
        assert len(canonical_by_group) == 2 and None not in canonical_by_group
        for event in suppressed:
            partner = canonical_by_group[event.dedup_group_id]
            assert partner.category_id == event.category_id
            assert partner.direction == event.direction
            assert abs(partner.ts - event.ts) <= 2000

        stats = dedup.stats
        assert (stats.matches, stats.demotions) == (2, 2)
        assert writer.written_events == 4
        assert writer.applied_demotes == 4  # 2 x (canonical tag + demotion)

    def test_first_wins_keeps_the_earliest_of_each_pair(self, tmp_path: Path) -> None:
        _generated, dedup, _writer, factory = _run_two_cam(tmp_path, strategy="first_wins")
        with factory() as session:
            events = session.scalars(select(Event)).all()
        assert len(events) == 4
        canonical = {e.dedup_group_id: e for e in events if e.is_canonical}
        suppressed = [e for e in events if not e.is_canonical]
        assert len(canonical) == 2 and len(suppressed) == 2
        for event in suppressed:
            partner = canonical[event.dedup_group_id]
            assert partner.ts <= event.ts  # first_wins: earliest is canonical
        # cam_b lags ~200 ms, so first_wins also lands on cam_a here.
        assert {e.camera_id for e in canonical.values()} == {CAM_A_ID}
        assert dedup.stats.matches == 2


class TestDocumentedLimitation:
    def test_same_category_three_seconds_apart_stays_two_canonical(self, tmp_path: Path) -> None:
        """Sightings further apart than the window are NOT merged (documented
        limitation): both events stay canonical with no dedup group."""
        db_path = str(tmp_path / "toskana.db")
        _seed_db(db_path, dedup_strategy="primary_wins")
        bus = EventBus()
        factory = make_writer_session_factory(db_path)
        with factory() as session:
            dedup = DedupEngine(load_camera_groups(session, RESTAURANT_ID), bus=bus)

        base_ts = int(time.time() * 1000)
        with EventWriter(factory, bus=bus) as writer:
            dedup.start()
            try:
                for camera_id, ts in ((CAM_A_ID, base_ts), (CAM_B_ID, base_ts + 3000)):
                    bus.publish(
                        TOPIC_CROSSING,
                        {
                            "id": new_event_id(),
                            "restaurant_id": RESTAURANT_ID,
                            "camera_id": camera_id,
                            "track_id": 1,
                            "category_id": CAT_DRINK,
                            "raw_class_name": "drink",
                            "confidence": 0.9,
                            "direction": "out",
                            "ts": ts,
                            "anchor_x": 0.5,
                            "anchor_y": 0.5,
                        },
                    )
            finally:
                dedup.stop()
        assert writer.written_events == 2
        assert dedup.stats.matches == 0
        with factory() as session:
            events = session.scalars(select(Event)).all()
        assert len(events) == 2
        assert all(e.is_canonical for e in events)  # both counted — by design
        assert all(e.dedup_group_id is None for e in events)
