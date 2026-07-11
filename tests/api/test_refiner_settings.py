"""Dashboard-managed refiner settings API (/api/settings/refiner):
masked GET, PUT persistence + live engine reconfiguration (key kept when
absent, cleared on empty string), and the POST /test connection probe
against an httpx.MockTransport-backed fake backend."""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv
from toskana.api.app import create_app
from toskana.api.routes import refiner_settings as routes_module
from toskana.config import AppConfig
from toskana.db.models import AppSetting
from toskana.settings_store import REFINER_SETTINGS_KEY

PUT_BODY: dict[str, Any] = {
    "provider": "openai_compatible",
    "model": "qwen2.5vl",
    "base_url": "http://localhost:11434/v1",
    "only_below_confidence": 0.65,
    "match_menu_items": True,
    "max_per_minute": 12,
}


@pytest.fixture(autouse=True)
def _no_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests hermetic: no ambient Anthropic key leaks into the routes."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)


@pytest.fixture()
def keyed_client(api_env: ApiEnv) -> Iterator[TestClient]:
    """A client whose config.yaml view has an anthropic key configured."""
    config = AppConfig(
        active_restaurant_slug="resta",
        db_path=api_env.db_path,
        snapshots_dir=str(api_env.snapshots_dir),
        detector_backend="synthetic",
        refiner_provider="anthropic",
        refiner_api_key="cfg-secret-key",
    )
    app = create_app(config, start_pipelines=False)
    with TestClient(app) as test_client:
        yield test_client


def _stored_settings(api_env: ApiEnv) -> dict[str, Any]:
    with api_env.session() as session:
        row = session.get(AppSetting, REFINER_SETTINGS_KEY)
        assert row is not None
        return dict(json.loads(row.value))


def _openai_reply(verdict: dict[str, Any]) -> dict[str, Any]:
    return {"choices": [{"message": {"content": json.dumps(verdict)}}]}


def _anthropic_reply(verdict: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(verdict)}]}


class TestGetSettings:
    def test_defaults_mirror_config(self, client: TestClient) -> None:
        response = client.get("/api/settings/refiner")
        assert response.status_code == 200
        body = response.json()
        assert body == {
            "provider": "off",
            "model": "claude-haiku-4-5",
            "base_url": "http://localhost:11434/v1",
            "has_api_key": False,
            "only_below_confidence": 1.0,
            "match_menu_items": True,
            "max_per_minute": 30,
        }

    def test_api_key_is_masked_never_returned(self, keyed_client: TestClient) -> None:
        body = keyed_client.get("/api/settings/refiner").json()
        assert body["provider"] == "anthropic"
        assert body["has_api_key"] is True
        assert "api_key" not in body
        assert "cfg-secret-key" not in json.dumps(body)


class TestPutSettings:
    def test_persists_and_reconfigures_engine(self, client: TestClient, api_env: ApiEnv) -> None:
        engine = api_env.app.state.refiner
        assert engine.stats.provider == "off" and engine.enabled is False

        response = client.put("/api/settings/refiner", json={**PUT_BODY, "api_key": "local-key"})
        assert response.status_code == 200
        body = response.json()
        assert body["provider"] == "openai_compatible"
        assert body["model"] == "qwen2.5vl"
        assert body["has_api_key"] is True
        assert "api_key" not in body and "local-key" not in json.dumps(body)

        # Applied to the running engine without a restart.
        stats = engine.stats
        assert stats.provider == "openai_compatible"
        assert stats.model == "qwen2.5vl"
        assert stats.enabled is True

        # Persisted to app_settings; GET reflects the stored row.
        assert _stored_settings(api_env)["provider"] == "openai_compatible"
        assert client.get("/api/settings/refiner").json()["only_below_confidence"] == 0.65

    def test_absent_api_key_keeps_stored_key(self, client: TestClient, api_env: ApiEnv) -> None:
        assert (
            client.put("/api/settings/refiner", json={**PUT_BODY, "api_key": "keep-me"}).status_code
            == 200
        )
        response = client.put("/api/settings/refiner", json={**PUT_BODY, "max_per_minute": 5})
        assert response.json()["has_api_key"] is True
        assert _stored_settings(api_env)["api_key"] == "keep-me"
        assert _stored_settings(api_env)["max_per_minute"] == 5

    def test_empty_api_key_clears_stored_key(self, client: TestClient, api_env: ApiEnv) -> None:
        client.put("/api/settings/refiner", json={**PUT_BODY, "api_key": "clear-me"})
        response = client.put("/api/settings/refiner", json={**PUT_BODY, "api_key": ""})
        assert response.json()["has_api_key"] is False
        assert _stored_settings(api_env)["api_key"] is None

    def test_switching_off_disables_engine(self, client: TestClient, api_env: ApiEnv) -> None:
        client.put("/api/settings/refiner", json=PUT_BODY)
        assert api_env.app.state.refiner.enabled is True
        client.put("/api/settings/refiner", json={**PUT_BODY, "provider": "off"})
        stats = api_env.app.state.refiner.stats
        assert stats.enabled is False and stats.provider == "off" and stats.model is None

    def test_unknown_provider_is_rejected(self, client: TestClient) -> None:
        response = client.put("/api/settings/refiner", json={**PUT_BODY, "provider": "gemini"})
        assert response.status_code == 422


class TestConnectionTest:
    def test_ok_path_returns_reply_and_latency(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            verdict = {
                "category_key": "drink",
                "menu_item_name": "Spritzer",
                "confidence": 0.9,
                "is_item": True,
            }
            return httpx.Response(200, json=_openai_reply(verdict))

        monkeypatch.setattr(routes_module, "TRANSPORT", httpx.MockTransport(handler))
        response = client.post("/api/settings/refiner/test", json=PUT_BODY)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["error"] is None
        assert isinstance(body["latency_ms"], int)
        assert body["reply"] == {
            "category_key": "drink",
            "menu_item_name": "Spritzer",
            "confidence": 0.9,
            "is_item": True,
        }

        # The temporary backend hit the posted base_url with the active
        # restaurant's categories + menu items and a real image payload.
        (request,) = seen
        assert str(request.url) == "http://localhost:11434/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["model"] == "qwen2.5vl"
        prompt = payload["messages"][0]["content"][1]["text"]
        assert "- drink:" in prompt and "- main:" in prompt and "Spritzer" in prompt
        assert "data:image/jpeg;base64," in payload["messages"][0]["content"][0]["image_url"]["url"]

    def test_failure_is_ok_false_with_friendly_error(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="model not found")

        monkeypatch.setattr(routes_module, "TRANSPORT", httpx.MockTransport(handler))
        response = client.post("/api/settings/refiner/test", json=PUT_BODY)
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False and body["reply"] is None
        assert "500" in body["error"]

    def test_saved_key_is_reused_when_none_posted(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client.put(
            "/api/settings/refiner",
            json={**PUT_BODY, "provider": "anthropic", "api_key": "sk-saved-123"},
        )
        seen_keys: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_keys.append(request.headers.get("x-api-key"))
            verdict = {"category_key": "main", "confidence": 0.8, "is_item": True}
            return httpx.Response(200, json=_anthropic_reply(verdict))

        monkeypatch.setattr(routes_module, "TRANSPORT", httpx.MockTransport(handler))
        response = client.post(
            "/api/settings/refiner/test",
            json={**PUT_BODY, "provider": "anthropic", "model": "claude-haiku-4-5"},
        )
        body = response.json()
        assert body["ok"] is True
        assert body["reply"]["category_key"] == "main"
        assert seen_keys == ["sk-saved-123"]
        assert "sk-saved-123" not in json.dumps(body)

    def test_saved_key_opt_out_yields_friendly_error(self, client: TestClient) -> None:
        client.put(
            "/api/settings/refiner",
            json={**PUT_BODY, "provider": "anthropic", "api_key": "sk-saved-123"},
        )
        response = client.post(
            "/api/settings/refiner/test",
            json={**PUT_BODY, "provider": "anthropic", "use_saved_api_key": False},
        )
        body = response.json()
        assert body["ok"] is False
        assert "API key" in body["error"]
        assert "sk-saved-123" not in json.dumps(body)

    def test_provider_off_is_a_friendly_error(self, client: TestClient) -> None:
        response = client.post("/api/settings/refiner/test", json={**PUT_BODY, "provider": "off"})
        body = response.json()
        assert response.status_code == 200
        assert body["ok"] is False and "off" in body["error"]

    def test_slow_backend_times_out_within_budget(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            time.sleep(0.6)
            return httpx.Response(200, json=_openai_reply({"is_item": True}))

        monkeypatch.setattr(routes_module, "TRANSPORT", httpx.MockTransport(handler))
        monkeypatch.setattr(routes_module, "TEST_TIMEOUT_S", 0.1)
        response = client.post("/api/settings/refiner/test", json=PUT_BODY)
        body = response.json()
        assert response.status_code == 200
        assert body["ok"] is False and "timed out" in body["error"]


class TestAuth:
    def test_settings_routes_require_bearer_token(self, api_env: ApiEnv) -> None:
        config = AppConfig(
            active_restaurant_slug="resta",
            db_path=api_env.db_path,
            snapshots_dir=str(api_env.snapshots_dir),
            detector_backend="synthetic",
            api_token="s3cret-token",
        )
        app = create_app(config, start_pipelines=False)
        with TestClient(app) as auth_client:
            assert auth_client.get("/api/settings/refiner").status_code == 401
            assert auth_client.put("/api/settings/refiner", json=PUT_BODY).status_code == 401
            assert auth_client.post("/api/settings/refiner/test", json=PUT_BODY).status_code == 401
            headers = {"Authorization": "Bearer s3cret-token"}
            assert auth_client.get("/api/settings/refiner", headers=headers).status_code == 200
