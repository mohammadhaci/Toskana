"""Shared fixtures for the API test suite.

Two tenants are seeded straight into a temporary SQLite file:

* restaurant A (``resta``, Europe/Vienna) — the active restaurant: two
  cameras with one line each, an exit group, three categories, a shared
  pretrained model with one mapping, a menu item, one A-owned model.
* restaurant B (``restb``) — one camera + line + category, used to prove
  tenant isolation (A's resources must 404 through B's URLs and vice versa).

The default app is created with ``start_pipelines=False`` (pure-CRUD tests);
the stream/WS end-to-end tests build their own app around a generated
synthetic video.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from toskana.api.app import create_app
from toskana.config import AppConfig
from toskana.db.base import Base, make_engine, make_session_factory
from toskana.db.models import (
    Camera,
    Category,
    ClassMapping,
    Event,
    ExitGroup,
    Line,
    MenuItem,
    ModelRegistry,
    Restaurant,
)
from toskana.events.writer import new_event_id


@dataclass
class ApiEnv:
    config: AppConfig
    app: Any
    db_path: str
    snapshots_dir: Path
    ids: dict[str, int]

    def session(self) -> Session:
        engine = make_engine(self.db_path)
        return make_session_factory(engine)()


def _seed_two_tenants(session: Session) -> dict[str, int]:
    ids: dict[str, int] = {}

    rest_a = Restaurant(slug="resta", name="Restaurant A", timezone="Europe/Vienna")
    rest_b = Restaurant(slug="restb", name="Restaurant B", timezone="Europe/Vienna")
    session.add_all([rest_a, rest_b])
    session.flush()
    ids["rest_a"], ids["rest_b"] = rest_a.id, rest_b.id

    cat_specs = [("drink", "Getränk", "Drink"), ("main", "Haupt", "Main"), ("dessert", "N", "D")]
    for key, de, en in cat_specs:
        cat = Category(restaurant_id=rest_a.id, key=key, name_de=de, name_en=en)
        session.add(cat)
        session.flush()
        ids[f"cat_a_{key}"] = cat.id
    cat_b = Category(restaurant_id=rest_b.id, key="drink", name_de="G", name_en="Drink")
    session.add(cat_b)
    session.flush()
    ids["cat_b_drink"] = cat_b.id

    group_a = ExitGroup(restaurant_id=rest_a.id, name="Pass A")
    group_b = ExitGroup(restaurant_id=rest_b.id, name="Pass B")
    session.add_all([group_a, group_b])
    session.flush()
    ids["group_a"], ids["group_b"] = group_a.id, group_b.id

    cam_a1 = Camera(
        restaurant_id=rest_a.id,
        name="A cam 1",
        source_type="file",
        source_url="./missing_a1.mp4",
        exit_group_id=group_a.id,
        is_primary_in_group=True,
    )
    cam_a2 = Camera(
        restaurant_id=rest_a.id, name="A cam 2", source_type="file", source_url="./missing_a2.mp4"
    )
    cam_b1 = Camera(
        restaurant_id=rest_b.id, name="B cam 1", source_type="file", source_url="./missing_b1.mp4"
    )
    session.add_all([cam_a1, cam_a2, cam_b1])
    session.flush()
    ids["cam_a1"], ids["cam_a2"], ids["cam_b1"] = cam_a1.id, cam_a2.id, cam_b1.id

    for key, cam in (("line_a1", cam_a1), ("line_a2", cam_a2), ("line_b1", cam_b1)):
        line = Line(
            restaurant_id=cam.restaurant_id, camera_id=cam.id, x1=0.5, y1=0.0, x2=0.5, y2=1.0
        )
        session.add(line)
        session.flush()
        ids[key] = line.id

    shared_model = ModelRegistry(
        restaurant_id=None,
        name="yolov8n",
        version="coco",
        kind="pretrained",
        path="yolov8n.pt",
        classes_json=json.dumps(["cup", "bowl"]),
        is_active=True,
    )
    own_model = ModelRegistry(
        restaurant_id=rest_a.id,
        name="resta-custom",
        version="1",
        kind="finetuned",
        path="models/resta/custom.pt",
        classes_json=json.dumps(["espresso"]),
        is_active=False,
    )
    session.add_all([shared_model, own_model])
    session.flush()
    ids["model_shared"], ids["model_a"] = shared_model.id, own_model.id

    mapping = ClassMapping(
        restaurant_id=rest_a.id,
        model_id=shared_model.id,
        model_class_id=0,
        model_class_name="cup",
        category_id=ids["cat_a_drink"],
        min_confidence=0.3,
    )
    session.add(mapping)
    session.flush()
    ids["mapping_a"] = mapping.id

    item = MenuItem(restaurant_id=rest_a.id, category_id=ids["cat_a_drink"], name="Spritzer")
    session.add(item)
    session.flush()
    ids["menu_a"] = item.id

    session.commit()
    return ids


@pytest.fixture()
def api_env(tmp_path: Path) -> ApiEnv:
    db_path = str(tmp_path / "api.db")
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        ids = _seed_two_tenants(session)
    engine.dispose()

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    config = AppConfig(
        active_restaurant_slug="resta",
        db_path=db_path,
        snapshots_dir=str(snapshots_dir),
        detector_backend="synthetic",
    )
    app = create_app(config, start_pipelines=False)
    return ApiEnv(config=config, app=app, db_path=db_path, snapshots_dir=snapshots_dir, ids=ids)


@pytest.fixture()
def client(api_env: ApiEnv) -> Iterator[TestClient]:
    with TestClient(api_env.app) as test_client:
        yield test_client


def add_event(
    session: Session,
    *,
    restaurant_id: int,
    camera_id: int,
    ts: int,
    direction: str = "out",
    category_id: int | None = None,
    is_canonical: bool = True,
    raw_class_name: str = "cup",
    snapshot_path: str | None = None,
    line_id: int | None = None,
) -> str:
    event_id = new_event_id()
    session.add(
        Event(
            id=event_id,
            restaurant_id=restaurant_id,
            camera_id=camera_id,
            line_id=line_id,
            track_id=1,
            category_id=category_id,
            raw_class_name=raw_class_name,
            confidence=0.9,
            direction=direction,
            ts=ts,
            anchor_x=0.5,
            anchor_y=0.5,
            snapshot_path=snapshot_path,
            is_canonical=is_canonical,
        )
    )
    return event_id
