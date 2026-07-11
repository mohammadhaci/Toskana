"""Optional bearer-token auth: 401 on every /api route (header or ?token=),
open GET /api/system/health, WS handshake rejection, and unchanged open
behavior when no token is configured."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tests.api.conftest import ApiEnv
from toskana.api.app import create_app
from toskana.config import AppConfig

TOKEN = "s3cret-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture()
def auth_client(api_env: ApiEnv) -> Iterator[TestClient]:
    """A client against the same seeded DB, but with api_token configured."""
    config = AppConfig(
        active_restaurant_slug="resta",
        db_path=api_env.db_path,
        snapshots_dir=str(api_env.snapshots_dir),
        detector_backend="synthetic",
        api_token=TOKEN,
    )
    app = create_app(config, start_pipelines=False)
    with TestClient(app) as test_client:
        yield test_client


class TestAuthDisabled:
    def test_default_lan_mode_stays_open(self, client: TestClient, api_env: ApiEnv) -> None:
        # No api_token configured: everything works without credentials.
        assert client.get("/api/restaurants").status_code == 200
        assert client.get("/api/system/info").status_code == 200
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"] == "hello"


class TestAuthEnabled:
    def test_api_routes_require_bearer_token(self, auth_client: TestClient) -> None:
        response = auth_client.get("/api/restaurants")
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"
        assert "token" in response.json()["detail"]

        assert auth_client.get("/api/system/info").status_code == 401
        assert auth_client.get("/api/restaurants", headers=AUTH).status_code == 200
        assert auth_client.get("/api/system/info", headers=AUTH).status_code == 200

    def test_wrong_or_malformed_token_401(self, auth_client: TestClient) -> None:
        assert (
            auth_client.get(
                "/api/restaurants", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )
        assert (
            auth_client.get("/api/restaurants", headers={"Authorization": TOKEN}).status_code == 401
        )
        assert auth_client.get(f"/api/restaurants?token=not-{TOKEN}").status_code == 401

    def test_health_stays_open_for_monitoring(self, auth_client: TestClient) -> None:
        response = auth_client.get("/api/system/health")
        assert response.status_code == 200
        assert response.json()["db_ok"] is True

    def test_mutations_are_protected(self, auth_client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        body = {"name": "Cam X", "source_type": "file", "source_url": "./x.mp4"}
        assert auth_client.post(f"/api/restaurants/{rid}/cameras", json=body).status_code == 401
        created = auth_client.post(f"/api/restaurants/{rid}/cameras", json=body, headers=AUTH)
        assert created.status_code == 201

    def test_stream_and_snapshot_protected_header_or_query(
        self, auth_client: TestClient, api_env: ApiEnv
    ) -> None:
        cam = api_env.ids["cam_a1"]
        # Unauthorized: 401 before any pipeline-state check.
        assert auth_client.get(f"/api/stream/{cam}").status_code == 401
        assert auth_client.get(f"/api/snapshot/{cam}").status_code == 401
        # Authorized (header or ?token= for <img> tags): auth passes, the
        # stopped pipeline then answers 409 as usual.
        assert auth_client.get(f"/api/snapshot/{cam}", headers=AUTH).status_code == 409
        assert auth_client.get(f"/api/stream/{cam}?token={TOKEN}").status_code == 409
        assert auth_client.get(f"/api/snapshot/{cam}?token={TOKEN}").status_code == 409

    def test_csv_export_accepts_query_token(self, auth_client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        assert auth_client.get(f"/api/restaurants/{rid}/events/export.csv").status_code == 401
        response = auth_client.get(f"/api/restaurants/{rid}/events/export.csv?token={TOKEN}")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/csv")

    def test_ws_requires_query_token(self, auth_client: TestClient) -> None:
        with pytest.raises(WebSocketDisconnect):
            with auth_client.websocket_connect("/ws/live"):
                pass  # pragma: no cover - handshake must be rejected
        with pytest.raises(WebSocketDisconnect):
            with auth_client.websocket_connect("/ws/live?token=wrong"):
                pass  # pragma: no cover
        with auth_client.websocket_connect(f"/ws/live?token={TOKEN}") as ws:
            assert ws.receive_json()["type"] == "hello"

    def test_dashboard_root_stays_public(self, auth_client: TestClient) -> None:
        # The SPA shell itself is served without a token; it prompts for one.
        assert auth_client.get("/").status_code == 200
