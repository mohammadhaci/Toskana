"""AI Event Refiner surface of the REST API: events expose the refined
fields; /system/health exposes the refiner status block (off by default)."""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy import update

from tests.api.conftest import ApiEnv, add_event
from toskana.db.models import Event


class TestEventsIncludeRefinerFields:
    def test_defaults_false_and_null(self, client: TestClient, api_env: ApiEnv) -> None:
        with api_env.session() as session:
            event_id = add_event(
                session, restaurant_id=api_env.ids["rest_a"], camera_id=api_env.ids["cam_a1"], ts=1
            )
            session.commit()

        listed = client.get(f"/api/restaurants/{api_env.ids['rest_a']}/events")
        assert listed.status_code == 200
        (item,) = listed.json()["items"]
        assert item["refined"] is False
        assert item["refiner_note"] is None

        single = client.get(f"/api/restaurants/{api_env.ids['rest_a']}/events/{event_id}")
        assert single.status_code == 200
        assert single.json()["refined"] is False

    def test_refined_event_carries_note(self, client: TestClient, api_env: ApiEnv) -> None:
        with api_env.session() as session:
            event_id = add_event(
                session, restaurant_id=api_env.ids["rest_a"], camera_id=api_env.ids["cam_a1"], ts=2
            )
            session.flush()
            note = "anthropic/claude-haiku-4-5, was=drink, verdict=main, is_item=true, conf=0.91"
            session.execute(
                update(Event).where(Event.id == event_id).values(refined=True, refiner_note=note)
            )
            session.commit()

        response = client.get(f"/api/restaurants/{api_env.ids['rest_a']}/events/{event_id}")
        assert response.status_code == 200
        body = response.json()
        assert body["refined"] is True
        assert "claude-haiku-4-5" in body["refiner_note"]


class TestSystemHealthRefinerBlock:
    def test_refiner_status_present_and_off_by_default(self, client: TestClient) -> None:
        response = client.get("/api/system/health")
        assert response.status_code == 200
        body = response.json()
        assert body["refiner_provider"] == "off"
        assert body["refiner_enabled"] is False
        assert body["refiner_model"] is None
        assert body["refiner_queue_size"] == 0
        assert body["refiner_refined"] == 0
        assert body["refiner_failures"] == 0
        assert body["refiner_skipped"] == 0
        assert body["refiner_last_error"] is None
