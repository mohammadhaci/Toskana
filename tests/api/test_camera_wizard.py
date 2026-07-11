"""Camera connection wizard endpoints: preset list + test-source probe."""

from __future__ import annotations

import base64
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv
from tests.tools.make_synthetic_video import generate_scenario
from toskana.api.routes import camera_wizard


class TestPresetList:
    def test_lists_vendor_presets(self, client: TestClient) -> None:
        response = client.get("/api/camera-presets")
        assert response.status_code == 200
        presets = {preset["key"]: preset for preset in response.json()}
        assert set(presets) >= {
            "hikvision",
            "hikvision_sub",
            "dahua",
            "dahua_sub",
            "reolink",
            "tplink",
            "uniview",
            "axis",
            "generic",
        }
        hik = presets["hikvision"]
        assert hik["default_port"] == 554
        assert hik["needs_channel"] is True
        assert "{password}" in hik["url_template"]
        assert presets["tplink"]["needs_channel"] is False
        assert presets["generic"]["url_template"] is None


class TestTestSource:
    def test_file_probe_returns_snapshot(
        self, client: TestClient, api_env: ApiEnv, tmp_path: Path
    ) -> None:
        video = generate_scenario("single_drink", tmp_path).video_paths["cam"]
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={"source_type": "file", "source_url": str(video)},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is True
        assert body["source_url"] == str(video)
        assert body["width"] > 0 and body["height"] > 0
        assert body["fps"] > 0
        raw = base64.b64decode(body["snapshot_b64"])
        assert raw.startswith(b"\xff\xd8")  # JPEG SOI
        image = cv2.imdecode(np.frombuffer(raw, dtype=np.uint8), cv2.IMREAD_COLOR)
        assert image is not None
        assert 0 < image.shape[1] <= 480  # downscaled preview

    def test_missing_file_is_a_friendly_failure(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={"source_type": "file", "source_url": "./does/not/exist.mp4"},
        )
        assert response.status_code == 200  # a probe result, not an HTTP error
        body = response.json()
        assert body["ok"] is False
        assert "path" in body["error"].lower()
        assert body["snapshot_b64"] is None

    def test_rtsp_failure_masks_password(
        self, client: TestClient, api_env: ApiEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(camera_wizard, "PROBE_TIMEOUT_S", 4.0)  # keep the test budget tight
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={
                "preset_key": "hikvision",
                "ip": "127.0.0.1",
                "port": 1,  # unroutable: nothing listens on tcp/1
                "username": "admin",
                "password": "sup3r@secret",
                "channel": 1,
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert body["error"]
        assert body["source_url_masked"] == "rtsp://admin:•••@127.0.0.1:1/Streaming/Channels/101"
        assert "sup3r" not in response.text  # the password never leaves the server

    def test_rtsp_manual_url_failure_masks_password(
        self, client: TestClient, api_env: ApiEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(camera_wizard, "PROBE_TIMEOUT_S", 4.0)
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={"source_type": "rtsp", "source_url": "rtsp://user:pw@127.0.0.1:1/x"},
        )
        body = response.json()
        assert body["ok"] is False
        assert "pw" not in body["source_url_masked"].split("@")[0].split(":")[-1]
        assert "user:•••@" in body["source_url_masked"]

    def test_preset_validation_error_is_a_probe_failure(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={"preset_key": "hikvision", "ip": "", "username": "u", "password": "p"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["ok"] is False
        assert "ip" in body["error"].lower() or "host" in body["error"].lower()

    def test_body_without_source_or_preset_is_422(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(f"/api/restaurants/{rid}/cameras/test-source", json={})
        assert response.status_code == 422

    def test_usb_index_must_be_numeric(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/cameras/test-source",
            json={"source_type": "usb", "source_url": "/dev/video0"},
        )
        assert response.status_code == 422

    def test_unknown_restaurant_404(self, client: TestClient) -> None:
        response = client.post(
            "/api/restaurants/999999/cameras/test-source",
            json={"source_type": "file", "source_url": "./x.mp4"},
        )
        assert response.status_code == 404
