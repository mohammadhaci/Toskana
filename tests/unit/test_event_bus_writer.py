"""Tests for the in-process event bus and the batched DB writer."""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any

from sqlalchemy import select

from toskana.db.base import Base, make_engine
from toskana.db.models import DataGap, Event
from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP, EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory, new_event_id


def event_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "restaurant_id": 1,
        "camera_id": 1,
        "line_id": None,
        "track_id": 3,
        "category_id": None,
        "menu_item_id": None,
        "raw_class_name": "drink",
        "confidence": 0.95,
        "direction": "out",
        "ts": 1_700_000_000_000,
        "frame_index": 42,
        "anchor_x": 0.5625,
        "anchor_y": 0.5,
        "snapshot_path": None,
        # extra live-consumer keys the writer must ignore:
        "canonical_direction": "positive",
        "class_name": "drink",
    }
    payload.update(overrides)
    return payload


def gap_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "restaurant_id": 1,
        "camera_id": 1,
        "from_ts": 1_700_000_000_000,
        "to_ts": 1_700_000_005_000,
        "reason": "reconnected after 2 attempt(s)",
    }
    payload.update(overrides)
    return payload


class TestEventBus:
    def test_publish_delivers_to_all_subscribers(self) -> None:
        bus = EventBus()
        got_a: list[Any] = []
        got_b: list[Any] = []
        bus.subscribe("t", got_a.append)
        bus.subscribe("t", got_b.append)
        assert bus.publish("t", {"x": 1}) == 2
        assert got_a == got_b == [{"x": 1}]

    def test_topics_are_isolated(self) -> None:
        bus = EventBus()
        got: list[Any] = []
        bus.subscribe("a", got.append)
        assert bus.publish("b", "payload") == 0
        assert got == []

    def test_unsubscribe(self) -> None:
        bus = EventBus()
        got: list[Any] = []
        unsubscribe = bus.subscribe("t", got.append)
        bus.publish("t", 1)
        unsubscribe()
        bus.publish("t", 2)
        assert got == [1]
        bus.unsubscribe("t", got.append)  # unknown fn: no-op

    def test_failing_subscriber_does_not_break_others(self) -> None:
        bus = EventBus()
        got: list[Any] = []

        def boom(_payload: Any) -> None:
            raise RuntimeError("boom")

        bus.subscribe("t", boom)
        bus.subscribe("t", got.append)
        assert bus.publish("t", 7) == 2
        assert got == [7]

    def test_concurrent_publish_is_thread_safe(self) -> None:
        bus = EventBus()
        got: list[int] = []
        lock = threading.Lock()

        def collect(payload: int) -> None:
            with lock:
                got.append(payload)

        bus.subscribe("t", collect)
        threads = [
            threading.Thread(target=lambda: [bus.publish("t", i) for i in range(100)])
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(got) == 400


def _prepare_db(tmp_path: Path) -> str:
    """Create the schema plus the FK targets (restaurant, camera) rows."""
    db_path = str(tmp_path / "writer.db")
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    from sqlalchemy.orm import Session

    from toskana.db.models import Camera, Restaurant

    with Session(engine) as session:
        session.add(Restaurant(id=1, slug="t", name="T"))
        session.flush()
        session.add(
            Camera(id=1, restaurant_id=1, name="cam", source_type="file", source_url="x.mp4")
        )
        session.commit()
    engine.dispose()
    return db_path


class TestEventWriter:
    def test_flush_on_stop_persists_everything(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        writer = EventWriter(factory, flush_interval_s=60.0)  # only the stop flush can fire
        writer.start()
        writer.enqueue_event(event_payload(track_id=1))
        writer.enqueue_event(event_payload(track_id=2, direction="in"))
        writer.enqueue_event(event_payload(track_id=3))
        writer.stop()
        assert writer.written_events == 3
        with factory() as session:
            rows = session.scalars(select(Event).order_by(Event.track_id)).all()
        assert [r.track_id for r in rows] == [1, 2, 3]
        assert [r.direction for r in rows] == ["out", "in", "out"]

    def test_event_fields_and_generated_ulid(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        with EventWriter(factory) as writer:
            writer.enqueue_event(event_payload())
        with factory() as session:
            (row,) = session.scalars(select(Event)).all()
        assert len(row.id) == 26  # generated ULID
        assert row.restaurant_id == 1
        assert row.camera_id == 1
        assert row.raw_class_name == "drink"
        assert row.confidence == 0.95
        assert row.direction == "out"
        assert row.ts == 1_700_000_000_000
        assert row.frame_index == 42
        assert row.anchor_x == 0.5625
        assert row.anchor_y == 0.5
        assert row.is_canonical is True

    def test_explicit_event_id_is_kept(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        event_id = new_event_id()
        with EventWriter(factory) as writer:
            writer.enqueue_event(event_payload(id=event_id))
        with factory() as session:
            (row,) = session.scalars(select(Event)).all()
        assert row.id == event_id

    def test_batched_persistence_via_bus(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        bus = EventBus()
        writer = EventWriter(factory, bus=bus, flush_interval_s=0.05, batch_size=10)
        writer.start()
        for track_id in range(120):  # crosses the batch threshold many times
            bus.publish(TOPIC_CROSSING, event_payload(track_id=track_id))
        deadline = time.monotonic() + 5.0
        while writer.written_events < 120 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert writer.written_events == 120  # flushed by batching, before stop
        writer.stop()
        with factory() as session:
            assert len(session.scalars(select(Event)).all()) == 120

    def test_interval_flush_without_batch_threshold(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        writer = EventWriter(factory, flush_interval_s=0.05, batch_size=1000)
        writer.start()
        writer.enqueue_event(event_payload())
        deadline = time.monotonic() + 5.0
        while writer.written_events < 1 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert writer.written_events == 1  # interval flush, not stop flush
        writer.stop()

    def test_data_gap_rows_from_bus(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        bus = EventBus()
        with EventWriter(factory, bus=bus) as writer:
            bus.publish(TOPIC_GAP, gap_payload())
            bus.publish(TOPIC_GAP, gap_payload(to_ts=None, reason="startup"))
        assert writer.written_gaps == 2
        with factory() as session:
            rows = session.scalars(select(DataGap).order_by(DataGap.id)).all()
        assert len(rows) == 2
        assert rows[0].from_ts == 1_700_000_000_000
        assert rows[0].to_ts == 1_700_000_005_000
        assert rows[0].reason == "reconnected after 2 attempt(s)"
        assert rows[1].to_ts is None

    def test_stop_unsubscribes_from_bus(self, tmp_path: Path) -> None:
        db_path = _prepare_db(tmp_path)
        factory = make_writer_session_factory(db_path)
        bus = EventBus()
        writer = EventWriter(factory, bus=bus)
        writer.start()
        writer.stop()
        assert bus.publish(TOPIC_CROSSING, event_payload()) == 0
        assert writer.written_events == 0
