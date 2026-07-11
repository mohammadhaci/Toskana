"""RefinerEngine: bus-driven vision-LLM verification of counted events —
DB row updates (category corrected, menu item matched case-insensitively),
WS correction publishing, confidence/off skips, and failure counting."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import pytest
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.base import Base, make_engine, make_session_factory
from toskana.db.models import Camera, Category, Event, MenuItem, Restaurant
from toskana.events.bus import TOPIC_CROSSING, TOPIC_REFINED, EventBus
from toskana.events.writer import make_writer_session_factory, new_event_id
from toskana.refiner import RefinerCategory, RefinerError, RefinerResult
from toskana.refiner_engine import RefinerEngine

FRAME_W, FRAME_H = 64, 48
BBOX = (10.0, 10.0, 30.0, 30.0)


@dataclass
class FakeBackend:
    """Records calls; returns a canned result or raises a canned error."""

    result: RefinerResult | Exception
    provider: str = "fake"
    model: str = "fake-vlm"
    calls: list[dict[str, Any]] = field(default_factory=list)

    def refine(
        self,
        image_jpeg: bytes,
        categories: list[RefinerCategory],
        menu_items: list[str],
    ) -> RefinerResult:
        self.calls.append(
            {"image_jpeg": image_jpeg, "categories": categories, "menu_items": menu_items}
        )
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


@dataclass
class Env:
    config: AppConfig
    bus: EventBus
    session_factory: sessionmaker[Session]
    ids: dict[str, int]
    snapshots_dir: Path

    def session(self) -> Session:
        return self.session_factory()


@pytest.fixture()
def env(tmp_path: Path) -> Env:
    db_path = str(tmp_path / "refiner.db")
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    ids: dict[str, int] = {}
    with make_session_factory(engine)() as session:
        restaurant = Restaurant(slug="resta", name="Restaurant A")
        session.add(restaurant)
        session.flush()
        ids["rest"] = restaurant.id
        drink = Category(
            restaurant_id=restaurant.id, key="drink", name_de="Getränk", name_en="Drink"
        )
        main = Category(restaurant_id=restaurant.id, key="main", name_de="Haupt", name_en="Main")
        session.add_all([drink, main])
        session.flush()
        ids["cat_drink"], ids["cat_main"] = drink.id, main.id
        item = MenuItem(restaurant_id=restaurant.id, category_id=main.id, name="Wiener Schnitzel")
        session.add(item)
        session.flush()
        ids["menu_schnitzel"] = item.id
        camera = Camera(
            restaurant_id=restaurant.id, name="Cam", source_type="file", source_url="./x.mp4"
        )
        session.add(camera)
        session.commit()
        ids["cam"] = camera.id
    engine.dispose()

    snapshots_dir = tmp_path / "snapshots"
    config = AppConfig(
        active_restaurant_slug="resta",
        db_path=db_path,
        snapshots_dir=str(snapshots_dir),
        refiner_provider="openai_compatible",  # any non-off provider enables the engine
        refiner_max_per_minute=10_000,  # keep tests fast
    )
    return Env(
        config=config,
        bus=EventBus(),
        session_factory=make_writer_session_factory(db_path),
        ids=ids,
        snapshots_dir=snapshots_dir,
    )


def _write_snapshot(env: Env, event_id: str) -> str:
    """A real tiny JPEG on disk; returns the relative snapshot path."""
    relative = Path("resta") / "2026-07-11" / f"{event_id}.jpg"
    path = env.snapshots_dir / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = np.full((FRAME_H, FRAME_W, 3), 200, dtype=np.uint8)
    frame[10:30, 10:30] = (0, 0, 255)  # the "item"
    assert cv2.imwrite(str(path), frame)
    return relative.as_posix()


def _insert_event(env: Env, event_id: str, snapshot_path: str, *, confidence: float = 0.4) -> None:
    with env.session() as session:
        session.add(
            Event(
                id=event_id,
                restaurant_id=env.ids["rest"],
                camera_id=env.ids["cam"],
                track_id=1,
                category_id=env.ids["cat_drink"],
                raw_class_name="cup",
                confidence=confidence,
                direction="out",
                ts=1_000,
                anchor_x=0.5,
                anchor_y=0.5,
                snapshot_path=snapshot_path,
            )
        )
        session.commit()


def _payload(env: Env, event_id: str, snapshot_path: str, *, confidence: float = 0.4) -> dict:
    return {
        "id": event_id,
        "restaurant_id": env.ids["rest"],
        "camera_id": env.ids["cam"],
        "category_id": env.ids["cat_drink"],
        "raw_class_name": "cup",
        "confidence": confidence,
        "direction": "out",
        "ts": 1_000,
        "snapshot_path": snapshot_path,
        "bbox_px": BBOX,
    }


def _run(env: Env, backend: FakeBackend, payloads: list[dict]) -> tuple[list[dict], Any]:
    """start -> publish -> stop (stop drains, so processing is finished).

    Returns the published ``refiner_correction`` messages and the stats.
    """
    corrections: list[dict] = []
    env.bus.subscribe(TOPIC_REFINED, corrections.append)
    engine = RefinerEngine(
        env.config, bus=env.bus, session_factory=env.session_factory, backend=backend
    )
    engine.start()
    try:
        for payload in payloads:
            env.bus.publish(TOPIC_CROSSING, payload)
    finally:
        engine.stop()
    return corrections, engine.stats


def _load_event(env: Env, event_id: str) -> Event:
    with env.session() as session:
        event = session.get(Event, event_id)
        assert event is not None
        return event


class TestRefinerEngine:
    def test_corrects_category_and_matches_menu_item_case_insensitively(self, env: Env) -> None:
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot)
        backend = FakeBackend(
            RefinerResult(
                category_key="main",
                menu_item_name="wiener SCHNITZEL",  # case-insensitive match
                confidence=0.91,
                is_item=True,
                raw_response="{}",
            )
        )

        messages, stats = _run(env, backend, [_payload(env, event_id, snapshot)])

        event = _load_event(env, event_id)
        assert event.category_id == env.ids["cat_main"]  # corrected drink -> main
        assert event.menu_item_id == env.ids["menu_schnitzel"]
        assert event.raw_class_name == "cup"  # never touched
        assert event.refined is True
        assert event.refiner_note is not None
        assert "fake/fake-vlm" in event.refiner_note
        assert "was=drink" in event.refiner_note
        assert "is_item=true" in event.refiner_note
        assert "conf=0.91" in event.refiner_note

        (correction,) = messages
        assert correction["event_id"] == event_id
        assert correction["category_id"] == env.ids["cat_main"]
        assert correction["previous_category_id"] == env.ids["cat_drink"]
        assert correction["menu_item_id"] == env.ids["menu_schnitzel"]
        assert correction["refined"] is True
        assert correction["direction"] == "out"

        assert stats.refined == 1 and stats.failures == 0

        # The backend got the crop (bbox + 15 % margin), not the full frame.
        (call,) = backend.calls
        image = cv2.imdecode(np.frombuffer(call["image_jpeg"], dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image.shape[0] < FRAME_H and image.shape[1] < FRAME_W
        assert image.shape[1] == pytest.approx(20 * 1.3, abs=2)
        # Categories + menu items came from the restaurant's catalog.
        assert [c.key for c in call["categories"]] == ["drink", "main"]
        assert call["menu_items"] == ["Wiener Schnitzel"]

    def test_skips_events_at_or_above_confidence_threshold(self, env: Env) -> None:
        env.config.refiner_only_below_confidence = 0.5
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot, confidence=0.9)
        backend = FakeBackend(RefinerResult("main", None, 1.0, is_item=True, raw_response="{}"))

        messages, stats = _run(env, backend, [_payload(env, event_id, snapshot, confidence=0.9)])

        assert backend.calls == []
        assert messages == []
        assert stats.skipped == 1 and stats.refined == 0
        assert _load_event(env, event_id).refined is False

    def test_provider_off_is_a_noop(self, env: Env) -> None:
        env.config.refiner_provider = "off"
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot)
        backend = FakeBackend(RefinerResult("main", None, 1.0, is_item=True, raw_response="{}"))

        messages, stats = _run(env, backend, [_payload(env, event_id, snapshot)])

        assert backend.calls == []
        assert messages == []
        assert stats.enabled is False and stats.provider == "off"
        assert _load_event(env, event_id).refined is False

    def test_unknown_category_key_changes_nothing_but_marks_refined(self, env: Env) -> None:
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot)
        backend = FakeBackend(RefinerResult("sushi", None, 0.8, is_item=True, raw_response="{}"))

        messages, _stats = _run(env, backend, [_payload(env, event_id, snapshot)])

        event = _load_event(env, event_id)
        assert event.category_id == env.ids["cat_drink"]  # unknown verdict: no change
        assert event.refined is True
        assert event.refiner_note is not None and "sushi (unknown)" in event.refiner_note
        (correction,) = messages
        assert correction["category_id"] == env.ids["cat_drink"]

    def test_is_item_false_keeps_counts_and_only_annotates(self, env: Env) -> None:
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot)
        backend = FakeBackend(
            RefinerResult(
                category_key=None,
                menu_item_name=None,
                confidence=0.95,
                is_item=False,
                raw_response="{}",
            )
        )

        messages, _stats = _run(env, backend, [_payload(env, event_id, snapshot)])

        event = _load_event(env, event_id)
        assert event.category_id == env.ids["cat_drink"]  # never deleted/changed silently
        assert event.is_canonical is True
        assert event.refined is True
        assert event.refiner_note is not None and "is_item=false" in event.refiner_note
        (correction,) = messages
        assert correction["is_item"] is False

    def test_backend_failure_is_counted_not_raised(self, env: Env) -> None:
        event_id = new_event_id()
        snapshot = _write_snapshot(env, event_id)
        _insert_event(env, event_id, snapshot)
        backend = FakeBackend(RefinerError("backend unreachable at http://localhost:11434"))

        messages, stats = _run(env, backend, [_payload(env, event_id, snapshot)])

        assert messages == []
        assert stats.failures == 1 and stats.refined == 0
        assert stats.last_error is not None and "unreachable" in stats.last_error
        assert _load_event(env, event_id).refined is False

    def test_missing_snapshot_file_is_a_failure_not_a_crash(self, env: Env) -> None:
        event_id = new_event_id()
        _insert_event(env, event_id, "resta/2026-07-11/missing.jpg")
        backend = FakeBackend(RefinerResult("main", None, 1.0, is_item=True, raw_response="{}"))

        messages, stats = _run(
            env, backend, [_payload(env, event_id, "resta/2026-07-11/missing.jpg")]
        )

        assert backend.calls == []
        assert stats.failures == 1
        assert stats.last_error is not None and "unreadable" in stats.last_error
