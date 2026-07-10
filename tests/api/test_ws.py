"""``/ws/live``: hello counters on connect, live crossing and gap fan-out
from the app's EventBus."""

from __future__ import annotations

import time

from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv, add_event
from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP
from toskana.events.writer import new_event_id

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
