"""``/ws/live``: hello counters on connect, live crossing/gap fan-out and M8
dedup ``correction`` messages from the app's EventBus."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import ApiEnv, add_event
from toskana.db.models import Event
from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP
from toskana.events.writer import new_event_id
from toskana.vision.dedup import DedupEngine, GroupConfig

#: Generous CI bound; the M4 target is <100ms and local runs stay well under it.
_DELIVERY_BUDGET_S = 2.0


def _crossing_payload(api_env: ApiEnv, event_id: str) -> dict:
    return {
        "id": event_id,
        "restaurant_id": api_env.ids["rest_a"],
        "camera_id": api_env.ids["cam_a1"],
        "line_id": api_env.ids["line_a1"],
        "track_id": 7,
        "category_id": api_env.ids["cat_a_drink"],
        "menu_item_id": None,
        "raw_class_name": "cup",
        "confidence": 0.87,
        "direction": "out",
        "ts": int(time.time() * 1000),
        "frame_index": 42,
        "anchor_x": 0.5,
        "anchor_y": 0.6,
        "snapshot_path": None,
        # Live-only extras the DB writer ignores:
        "canonical_direction": "positive",
        "class_name": "cup",
    }


class TestWsLive:
    def test_hello_carries_today_counters(self, client: TestClient, api_env: ApiEnv) -> None:
        # One canonical drink 'out' today so the hello has a non-zero counter.
        with api_env.session() as session:
            add_event(
                session,
                restaurant_id=api_env.ids["rest_a"],
                camera_id=api_env.ids["cam_a1"],
                ts=int(time.time() * 1000),
                category_id=api_env.ids["cat_a_drink"],
            )
            session.commit()
        with client.websocket_connect("/ws/live") as ws:
            hello = ws.receive_json()
        assert hello["type"] == "hello"
        by_key = {counter["key"]: counter for counter in hello["counters"]}
        assert set(by_key) == {"drink", "main", "dessert"}  # active restaurant A only
        assert (by_key["drink"]["out"], by_key["drink"]["in"], by_key["drink"]["net"]) == (1, 0, 1)
        assert by_key["main"]["out"] == 0

    def test_crossing_and_gap_are_forwarded_quickly(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        bus = api_env.app.state.bus
        event_id = new_event_id()
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"] == "hello"

            published_at = time.monotonic()
            bus.publish(TOPIC_CROSSING, _crossing_payload(api_env, event_id))
            message = ws.receive_json()
            elapsed = time.monotonic() - published_at

            assert message["type"] == "crossing"
            assert elapsed < _DELIVERY_BUDGET_S
            event = message["event"]
            assert event["id"] == event_id
            assert event["direction"] == "out"
            assert event["category_id"] == api_env.ids["cat_a_drink"]
            assert event["class_name"] == "cup"
            assert "canonical_direction" not in event  # internal extras are dropped

            gap_payload = {
                "restaurant_id": api_env.ids["rest_a"],
                "camera_id": api_env.ids["cam_a1"],
                "from_ts": 1_000,
                "to_ts": 2_000,
                "reason": "test_outage",
            }
            bus.publish(TOPIC_GAP, gap_payload)
            gap_message = ws.receive_json()
            assert gap_message["type"] == "gap"
            assert gap_message["gap"] == gap_payload

    def test_correction_is_broadcast_when_dedup_demotes(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        """Full demotion path against the app's bus: two same-category
        crossings from two exit-group cameras -> the DedupEngine demotes one,
        the writer applies the UPDATE and /ws/live emits {type: correction}."""
        bus = api_env.app.state.bus
        config = GroupConfig(
            group_id=api_env.ids["group_a"],
            window_ms=2000,
            strategy="primary_wins",
            primary_camera_id=api_env.ids["cam_a1"],
        )
        engine = DedupEngine(
            {api_env.ids["cam_a1"]: config, api_env.ids["cam_a2"]: config}, bus=bus
        )
        engine.start()  # after lifespan startup: the writer is subscribed first
        try:
            canonical_id, demoted_id = new_event_id(), new_event_id()
            with client.websocket_connect("/ws/live") as ws:
                assert ws.receive_json()["type"] == "hello"

                first = _crossing_payload(api_env, canonical_id)
                second = dict(
                    _crossing_payload(api_env, demoted_id),
                    camera_id=api_env.ids["cam_a2"],
                    ts=first["ts"] + 200,
                )
                bus.publish(TOPIC_CROSSING, first)
                assert ws.receive_json()["type"] == "crossing"
                bus.publish(TOPIC_CROSSING, second)
                assert ws.receive_json()["type"] == "crossing"

                correction = ws.receive_json()
            assert correction["type"] == "correction"
            assert correction["event_id"] == demoted_id  # non-primary camera lost
            assert correction["is_canonical"] is False
            assert len(correction["dedup_group_id"]) == 26
            assert correction["category_id"] == api_env.ids["cat_a_drink"]
            assert correction["direction"] == "out"
            assert correction["camera_id"] == api_env.ids["cam_a2"]

            # The single-writer demotion UPDATE lands shortly after the insert.
            deadline = time.monotonic() + _DELIVERY_BUDGET_S
            while True:
                with api_env.session() as session:
                    rows = {
                        row.id: row
                        for row in session.scalars(
                            select(Event).where(Event.id.in_([canonical_id, demoted_id]))
                        )
                    }
                if len(rows) == 2 and rows[demoted_id].is_canonical is False:
                    break
                assert time.monotonic() < deadline, "demotion UPDATE never applied"
                time.sleep(0.05)
            assert rows[canonical_id].is_canonical is True
            assert (
                rows[canonical_id].dedup_group_id
                == rows[demoted_id].dedup_group_id
                == correction["dedup_group_id"]
            )
        finally:
            engine.stop()

    def test_two_clients_both_receive(self, client: TestClient, api_env: ApiEnv) -> None:
        bus = api_env.app.state.bus
        with (
            client.websocket_connect("/ws/live") as ws_one,
            client.websocket_connect("/ws/live") as ws_two,
        ):
            assert ws_one.receive_json()["type"] == "hello"
            assert ws_two.receive_json()["type"] == "hello"
            bus.publish(TOPIC_CROSSING, _crossing_payload(api_env, new_event_id()))
            assert ws_one.receive_json()["type"] == "crossing"
            assert ws_two.receive_json()["type"] == "crossing"
