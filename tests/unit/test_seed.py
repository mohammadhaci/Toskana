"""Seed content and idempotency."""

from __future__ import annotations

import json

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from toskana.db.models import (
    Camera,
    Category,
    ClassMapping,
    ExitGroup,
    Line,
    ModelRegistry,
    Restaurant,
)
from toskana.db.seed import CLASS_TO_CATEGORY, COCO_CLASSES, seed


def _counts(session: Session) -> dict[str, int]:
    return {
        cls.__tablename__: session.scalar(select(func.count()).select_from(cls)) or 0
        for cls in (Restaurant, Category, Camera, ExitGroup, Line, ModelRegistry, ClassMapping)
    }


def test_seed_creates_expected_rows(session: Session) -> None:
    restaurant = seed(session)
    assert restaurant.slug == "toskana"
    assert restaurant.name == "Trattoria Toskana"
    assert restaurant.timezone == "Europe/Vienna"
    assert restaurant.locale_default == "de"

    assert _counts(session) == {
        "restaurants": 1,
        "categories": 6,
        "cameras": 2,
        "exit_groups": 1,
        "lines": 2,
        "models_registry": 1,
        "class_mappings": len(CLASS_TO_CATEGORY),
    }

    keys = set(session.scalars(select(Category.key)))
    assert keys == {"drink", "coffee", "main", "starter", "dessert", "side"}

    cameras = session.scalars(select(Camera).order_by(Camera.id)).all()
    assert all(c.source_type == "file" for c in cameras)
    assert [c.is_primary_in_group for c in cameras] == [True, False]
    group = session.scalars(select(ExitGroup)).one()
    assert all(c.exit_group_id == group.id for c in cameras)
    assert group.dedup_window_ms == 2000
    assert group.dedup_strategy == "primary_wins"

    for line in session.scalars(select(Line)):
        for coord in (line.x1, line.y1, line.x2, line.y2):
            assert 0.0 <= coord <= 1.0
        assert (line.x1, line.y1) != (line.x2, line.y2)


def test_seed_model_and_mappings(session: Session) -> None:
    restaurant = seed(session)
    model = session.scalars(select(ModelRegistry)).one()
    assert model.restaurant_id is None  # shared pretrained model
    assert model.kind == "pretrained"
    assert model.name == "yolov8n"
    classes = json.loads(model.classes_json)
    assert classes == COCO_CLASSES
    assert len(classes) == 80

    mappings = session.scalars(select(ClassMapping)).all()
    by_name = {m.model_class_name: m for m in mappings}
    assert set(by_name) == set(CLASS_TO_CATEGORY)
    categories = {c.id: c.key for c in session.scalars(select(Category))}
    for name, mapping in by_name.items():
        assert mapping.restaurant_id == restaurant.id
        assert mapping.model_id == model.id
        assert mapping.model_class_id == COCO_CLASSES.index(name)
        assert mapping.min_confidence == 0.35
        assert mapping.menu_item_id is None
        assert mapping.category_id is not None
        assert categories[mapping.category_id] == CLASS_TO_CATEGORY[name]


def test_seed_is_idempotent(session: Session) -> None:
    seed(session)
    first = _counts(session)
    seed(session)
    seed(session)
    assert _counts(session) == first
