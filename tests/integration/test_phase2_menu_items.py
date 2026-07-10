"""M11 / Phase 2: named menu items end-to-end WITHOUT any trained model.

The synthetic backend plays a Phase-2 model: ``class_mappings`` rows map the
synthetic classes straight onto menu items ("Aperol Spritz" drink item,
"Wiener Schnitzel" main item). Persisted events must carry BOTH the
``menu_item_id`` and the item's ``category_id`` (fallback chain, critic
requirement #10), proven through the pipeline/bus/writer stack and through
the ``toskana simulate --db`` CLI path.
"""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.tools.make_synthetic_video import generate_scenario
from toskana.cli import build_parser, cmd_simulate
from toskana.config import AppConfig
from toskana.db.base import Base, make_engine
from toskana.db.models import (
    Camera,
    Category,
    ClassMapping,
    Event,
    MenuItem,
    ModelRegistry,
    Restaurant,
)
from toskana.events.bus import EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory
from toskana.vision.line_crossing import LineSpec
from toskana.vision.mapping import MappingRule, MenuItemInfo
from toskana.vision.pipeline import CameraPipeline, PipelineSpec

#: Scenario line: vertical at x=0.5, drawn top->bottom => positive = left->right.
LINE = LineSpec(x1=0.5, y1=0.0, x2=0.5, y2=1.0, line_id=None)


def _seed_phase2_db(db_path: str, *, slug: str) -> dict[str, int]:
    """Restaurant + categories + menu items + item-targeting class mappings."""
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        restaurant = Restaurant(id=1, slug=slug, name="Testaurant")
        session.add(restaurant)
        session.flush()
        session.add(
            Camera(id=1, restaurant_id=1, name="cam", source_type="file", source_url="scenario")
        )
        drink_cat = Category(restaurant_id=1, key="drink", name_de="Getränk", name_en="Drink")
        main_cat = Category(restaurant_id=1, key="main", name_de="Haupt", name_en="Main")
        session.add_all([drink_cat, main_cat])
        session.flush()
        spritz = MenuItem(restaurant_id=1, category_id=drink_cat.id, name="Aperol Spritz")
        schnitzel = MenuItem(restaurant_id=1, category_id=main_cat.id, name="Wiener Schnitzel")
        session.add_all([spritz, schnitzel])
        session.flush()
        model = ModelRegistry(
            restaurant_id=1,
            name="phase2-demo",
            version="1",
            kind="finetuned",
            path="models/testaurant/phase2-demo.pt",
            classes_json=json.dumps(["drink", "main", "dessert"]),
            is_active=True,
        )
        session.add(model)
        session.flush()
        # Menu-item targets only: NO category_id — it must be derived.
        session.add_all(
            [
                ClassMapping(
                    restaurant_id=1,
                    model_id=model.id,
                    model_class_id=0,
                    model_class_name="drink",
                    menu_item_id=spritz.id,
                    min_confidence=0.35,
                ),
                ClassMapping(
                    restaurant_id=1,
                    model_id=model.id,
                    model_class_id=1,
                    model_class_name="main",
                    menu_item_id=schnitzel.id,
                    min_confidence=0.35,
                ),
            ]
        )
        session.commit()
        ids = {
            "drink_cat": drink_cat.id,
            "main_cat": main_cat.id,
            "spritz": spritz.id,
            "schnitzel": schnitzel.id,
        }
    engine.dispose()
    return ids


class TestPipelinePersistsMenuItems:
    def test_tray_carry_3_events_carry_item_and_derived_category(self, tmp_path: Path) -> None:
        """tray_carry_3 (1 drink + 2 mains) with item-targeting mappings ->
        3 persisted events, each with menu_item_id AND the item's category."""
        db_path = str(tmp_path / "phase2.db")
        ids = _seed_phase2_db(db_path, slug="testaurant")
        generated = generate_scenario("tray_carry_3", tmp_path)

        rules = (
            MappingRule(
                model_class_id=0,
                model_class_name="drink",
                menu_item_id=ids["spritz"],
                min_confidence=0.35,
            ),
            MappingRule(
                model_class_id=1,
                model_class_name="main",
                menu_item_id=ids["schnitzel"],
                min_confidence=0.35,
            ),
        )
        menu_items = (
            MenuItemInfo(
                menu_item_id=ids["spritz"], category_id=ids["drink_cat"], name="Aperol Spritz"
            ),
            MenuItemInfo(
                menu_item_id=ids["schnitzel"],
                category_id=ids["main_cat"],
                name="Wiener Schnitzel",
            ),
        )
        spec = PipelineSpec(
            restaurant_id=1,
            camera_id=1,
            source=str(generated.video_paths["cam"]),
            restaurant_slug="testaurant",
            backend="synthetic",
            paced=False,
            lines=(LINE,),
            mapping_rules=rules,
            menu_items=menu_items,
        )
        bus = EventBus()
        factory = make_writer_session_factory(db_path)
        with EventWriter(factory, bus=bus) as writer:
            result = CameraPipeline(spec, bus=bus).run_once()
        assert result.total == 3
        assert writer.written_events == 3

        # Live payloads carry the display name for the WS ticker.
        names = sorted(payload["menu_item_name"] for payload in result.payloads)
        assert names == ["Aperol Spritz", "Wiener Schnitzel", "Wiener Schnitzel"]

        with factory() as session:
            rows = session.scalars(select(Event).order_by(Event.ts)).all()
        assert len(rows) == 3
        by_item = {(row.menu_item_id, row.category_id) for row in rows}
        assert by_item == {
            (ids["spritz"], ids["drink_cat"]),  # drink item + derived drink category
            (ids["schnitzel"], ids["main_cat"]),  # main item + derived main category
        }
        assert sum(row.menu_item_id == ids["schnitzel"] for row in rows) == 2
        assert all(row.direction == "out" and row.is_canonical for row in rows)


class TestSimulateCliMenuItems:
    def test_simulate_db_persists_menu_item_and_category(self, tmp_path: Path) -> None:
        """`toskana simulate --db` end-to-end: the synthetic class 'drink' is
        mapped to the 'Aperol Spritz' menu item in the DB; the persisted event
        carries menu_item_id + the item's (derived) category_id."""
        db_path = str(tmp_path / "simulate.db")
        ids = _seed_phase2_db(db_path, slug="toskana")  # AppConfig default slug
        generated = generate_scenario("single_drink", tmp_path)
        video = str(generated.video_paths["cam"])

        config = AppConfig(
            active_restaurant_slug="toskana",
            db_path=db_path,
            snapshots_dir=str(tmp_path / "snapshots"),
            detector_backend="synthetic",
        )
        args = build_parser().parse_args(["simulate", "--video", video, "--db", db_path, "--json"])
        assert cmd_simulate(config, args) == 0

        engine = make_engine(db_path)
        with Session(engine) as session:
            (event,) = session.scalars(select(Event)).all()
            assert event.menu_item_id == ids["spritz"]
            assert event.category_id == ids["drink_cat"]  # derived, not stored on the mapping
            assert event.menu_item.name == "Aperol Spritz"
            assert event.raw_class_name == "drink"
            assert event.direction == "out"
            assert event.is_canonical
        engine.dispose()
