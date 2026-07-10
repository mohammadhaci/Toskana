"""Schema tests: table creation, relationships, constraints, cascades, indexes."""

from __future__ import annotations

import json

from pytest import raises
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ulid import ULID

from toskana.db.base import Base
from toskana.db.models import (
    Camera,
    Category,
    ClassMapping,
    CountingEvalRun,
    DataGap,
    Event,
    ExitGroup,
    Line,
    MenuItem,
    ModelRegistry,
    Restaurant,
    ServiceSession,
)

EXPECTED_TABLES = {
    "restaurants",
    "cameras",
    "exit_groups",
    "lines",
    "categories",
    "menu_items",
    "class_mappings",
    "models_registry",
    "events",
    "sessions",
    "data_gaps",
    "counting_eval_runs",
    "reconcile_runs",
}


def _make_full_graph(session: Session) -> Restaurant:
    """Insert one row in every table, wired through relationships."""
    restaurant = Restaurant(slug="r1", name="Ristorante Uno")
    group = ExitGroup(restaurant=restaurant, name="Pass 1")
    camera = Camera(
        restaurant=restaurant,
        exit_group=group,
        name="Cam 1",
        source_type="file",
        source_url="./video.mp4",
        is_primary_in_group=True,
    )
    line = Line(restaurant=restaurant, camera=camera, x1=0.1, y1=0.1, x2=0.9, y2=0.9)
    category = Category(restaurant=restaurant, key="drink", name_de="Getränk", name_en="Drink")
    item = MenuItem(restaurant=restaurant, category=category, name="Spritz", price=4.5)
    model = ModelRegistry(
        restaurant_id=None,
        name="yolov8n",
        kind="pretrained",
        path="yolov8n.pt",
        classes_json=json.dumps(["cup"]),
    )
    mapping = ClassMapping(
        restaurant=restaurant,
        model=model,
        model_class_id=41,
        model_class_name="cup",
        category=category,
    )
    shift = ServiceSession(restaurant=restaurant, name="Lunch", started_ts=1_700_000_000_000)
    session.add_all([restaurant, group, camera, line, category, item, model, mapping, shift])
    session.flush()

    event = Event(
        id=str(ULID()),
        restaurant=restaurant,
        camera=camera,
        line=line,
        session=shift,
        track_id=7,
        category=category,
        raw_class_name="cup",
        confidence=0.9,
        direction="out",
        ts=1_700_000_100_000,
        frame_index=42,
        anchor_x=0.5,
        anchor_y=0.6,
        snapshot_path="data/snapshots/x.jpg",
    )
    gap = DataGap(
        restaurant_id=restaurant.id,
        camera_id=camera.id,
        from_ts=1_700_000_000_000,
        to_ts=1_700_000_050_000,
        reason="startup",
    )
    run = CountingEvalRun(
        restaurant_id=restaurant.id,
        created_ts=1_700_000_200_000,
        name="baseline",
        metrics_json=json.dumps({"precision": 1.0}),
    )
    session.add_all([event, gap, run])
    session.commit()
    return restaurant


def test_all_tables_created(engine: Engine) -> None:
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())
    assert EXPECTED_TABLES == set(Base.metadata.tables.keys())


def test_insert_rows_through_relationships(session: Session) -> None:
    restaurant = _make_full_graph(session)
    assert restaurant.id is not None
    assert len(restaurant.cameras) == 1
    assert restaurant.cameras[0].lines[0].x2 == 0.9
    assert restaurant.cameras[0].exit_group.name == "Pass 1"
    event = session.scalars(select(Event)).one()
    assert event.is_canonical is True
    assert event.category.key == "drink"
    assert len(event.id) == 26  # ULID string PK


def test_restaurant_slug_unique(session: Session) -> None:
    session.add(Restaurant(slug="dup", name="A"))
    session.commit()
    session.add(Restaurant(slug="dup", name="B"))
    with raises(IntegrityError):
        session.commit()
    session.rollback()


def test_class_mapping_unique_per_model_class_restaurant(session: Session) -> None:
    restaurant = _make_full_graph(session)
    model = session.scalars(select(ModelRegistry)).one()
    category = session.scalars(select(Category)).one()
    session.add(
        ClassMapping(
            restaurant_id=restaurant.id,
            model_id=model.id,
            model_class_id=41,  # same (model, class, restaurant) as seeded mapping
            model_class_name="cup",
            category_id=category.id,
        )
    )
    with raises(IntegrityError):
        session.commit()
    session.rollback()


def test_class_mapping_requires_category_or_menu_item(session: Session) -> None:
    restaurant = _make_full_graph(session)
    model = session.scalars(select(ModelRegistry)).one()
    session.add(
        ClassMapping(
            restaurant_id=restaurant.id,
            model_id=model.id,
            model_class_id=99,
            model_class_name="orphan",
        )
    )
    with raises(IntegrityError):
        session.commit()
    session.rollback()


def test_foreign_keys_enforced(session: Session) -> None:
    session.add(Camera(restaurant_id=12345, name="ghost", source_type="usb", source_url="0"))
    with raises(IntegrityError):
        session.commit()
    session.rollback()


def test_db_level_cascade_on_restaurant_delete(session: Session) -> None:
    restaurant = _make_full_graph(session)
    # Raw SQL delete bypasses ORM cascades — exercises ON DELETE at DB level.
    session.execute(text("DELETE FROM restaurants WHERE id = :rid"), {"rid": restaurant.id})
    session.commit()
    for model_cls in (
        Camera,
        Line,
        Category,
        MenuItem,
        ClassMapping,
        Event,
        ServiceSession,
        DataGap,
        CountingEvalRun,
        ExitGroup,
    ):
        assert session.scalar(select(func.count()).select_from(model_cls)) == 0
    # Shared model (restaurant_id NULL) survives.
    assert session.scalar(select(func.count()).select_from(ModelRegistry)) == 1


def test_camera_delete_sets_exit_group_null_semantics(session: Session) -> None:
    restaurant = _make_full_graph(session)
    session.execute(text("DELETE FROM exit_groups"))
    session.commit()
    session.expire_all()  # raw SQL bypassed the ORM; drop stale identity-map state
    camera = session.scalars(select(Camera)).one()
    assert camera.restaurant_id == restaurant.id
    assert camera.exit_group_id is None  # ON DELETE SET NULL


def test_events_indexes_exist(engine: Engine) -> None:
    index_names = {ix["name"] for ix in inspect(engine).get_indexes("events")}
    assert {
        "ix_events_restaurant_ts",
        "ix_events_camera_ts",
        "ix_events_dedup_group",
        "ix_events_restaurant_canonical_ts",
    } <= index_names


def test_event_direction_constrained(session: Session) -> None:
    restaurant = _make_full_graph(session)
    camera = session.scalars(select(Camera)).one()
    session.add(
        Event(
            id=str(ULID()),
            restaurant_id=restaurant.id,
            camera_id=camera.id,
            track_id=1,
            raw_class_name="cup",
            confidence=0.5,
            direction="sideways",
            ts=0,
            anchor_x=0.5,
            anchor_y=0.5,
        )
    )
    with raises(IntegrityError):
        session.commit()
    session.rollback()
