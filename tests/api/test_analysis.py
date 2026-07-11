"""Video-analysis jobs: upload/URL submission, background counting through
the synthetic backend, event persistence, tenant isolation and cancel."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import ApiEnv
from tests.tools.make_synthetic_video import generate_scenario
from toskana.db.models import Camera, Event, Line


def _poll(
    fetch: Callable[[], dict[str, Any]],
    done: Callable[[dict[str, Any]], bool],
    *,
    timeout_s: float = 60.0,
) -> dict[str, Any]:
    """Poll ``fetch`` until ``done(job)`` or timeout; returns the last job."""
    deadline = time.monotonic() + timeout_s
    job = fetch()
    while not done(job):
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for analysis job: {job}")
        time.sleep(0.1)
        job = fetch()
    return job


def _submit_video(
    client: TestClient, restaurant_id: int, video: Path, **form: str
) -> dict[str, Any]:
    with video.open("rb") as handle:
        response = client.post(
            f"/api/restaurants/{restaurant_id}/analysis",
            files={"file": (video.name, handle, "video/mp4")},
            data=form,
        )
    assert response.status_code == 201, response.text
    return response.json()


class TestUploadAnalysis:
    def test_synthetic_upload_counts_and_persists_events(
        self, client: TestClient, api_env: ApiEnv, tmp_path: Path
    ) -> None:
        generated = generate_scenario("tray_carry_3", tmp_path / "scenarios")
        video = generated.video_paths["cam"]
        rid = api_env.ids["rest_a"]

        job = _submit_video(client, rid, video, backend="synthetic")
        assert job["status"] in ("queued", "running")
        assert job["backend"] == "synthetic"
        assert job["video_name"].endswith(video.suffix)
        job_id = job["id"]

        def fetch() -> dict[str, Any]:
            response = client.get(f"/api/restaurants/{rid}/analysis/{job_id}")
            assert response.status_code == 200
            return response.json()

        job = _poll(fetch, lambda j: j["status"] in ("done", "error"))
        assert job["status"] == "done", job["error"]

        # tray_carry_3 ground truth: 3 positive crossings (1 drink, 2 mains).
        assert job["counts"]["out"] == {"drink": 1, "main": 2}
        assert job["counts"]["in"] == {}
        assert job["total_out"] == 3
        assert job["total_in"] == 0
        assert job["frames_total"] and job["frames_done"] > 0
        assert job["frames_done"] <= job["frames_total"]
        assert job["error"] is None
        assert job["finished_ts"] is not None

        # A dedicated disabled file camera + the default vertical line exist.
        camera_id, line_id = job["camera_id"], job["line_id"]
        with api_env.session() as session:
            camera = session.get(Camera, camera_id)
            assert camera is not None
            assert camera.restaurant_id == rid
            assert camera.enabled is False
            assert camera.source_type == "file"
            assert camera.name.startswith("Analyse:")
            line = session.get(Line, line_id)
            assert line is not None
            assert (line.x1, line.y1, line.x2, line.y2) == (0.5, 0.0, 0.5, 1.0)

        # Events reach the DB through the normal writer (async flush) with
        # snapshots on disk, and are visible on the standard events endpoint.
        def db_events() -> list[Event]:
            with api_env.session() as session:
                return list(session.scalars(select(Event).where(Event.camera_id == camera_id)))

        deadline = time.monotonic() + 10.0
        events = db_events()
        while len(events) < 3 and time.monotonic() < deadline:
            time.sleep(0.1)
            events = db_events()
        assert len(events) == 3
        drink_id = api_env.ids["cat_a_drink"]
        main_id = api_env.ids["cat_a_main"]
        assert sorted(event.category_id for event in events) == sorted([drink_id, main_id, main_id])
        for event in events:
            assert event.restaurant_id == rid
            assert event.direction == "out"
            assert event.line_id == line_id
            assert event.snapshot_path is not None
            assert (api_env.snapshots_dir / event.snapshot_path).is_file()

        listed = client.get(
            f"/api/restaurants/{rid}/events", params={"camera_id": camera_id}
        ).json()
        assert listed["total"] == 3

        # Jobs list contains the job, newest first.
        jobs = client.get(f"/api/restaurants/{rid}/analysis").json()
        assert [j["id"] for j in jobs] == [job_id]

        # Cancel after completion is a 200 no-op (stays done).
        cancelled = client.post(f"/api/restaurants/{rid}/analysis/{job_id}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "done"

    def test_tenant_isolation(self, client: TestClient, api_env: ApiEnv, tmp_path: Path) -> None:
        generated = generate_scenario("single_drink", tmp_path / "scenarios")
        video = generated.video_paths["cam"]
        rid_a, rid_b = api_env.ids["rest_a"], api_env.ids["rest_b"]

        job = _submit_video(client, rid_a, video, backend="synthetic")

        # B cannot see A's job: detail 404s, list stays empty, cancel 404s.
        assert client.get(f"/api/restaurants/{rid_b}/analysis/{job['id']}").status_code == 404
        assert client.get(f"/api/restaurants/{rid_b}/analysis").json() == []
        assert (
            client.post(f"/api/restaurants/{rid_b}/analysis/{job['id']}/cancel").status_code == 404
        )
        # Unknown restaurant 404s outright.
        assert client.get("/api/restaurants/999999/analysis").status_code == 404


class TestValidation:
    def test_neither_file_nor_url_is_422(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(f"/api/restaurants/{rid}/analysis", data={})
        assert response.status_code == 422

    def test_unknown_backend_is_422(
        self, client: TestClient, api_env: ApiEnv, tmp_path: Path
    ) -> None:
        rid = api_env.ids["rest_a"]
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"not really a video")
        with video.open("rb") as handle:
            response = client.post(
                f"/api/restaurants/{rid}/analysis",
                files={"file": ("clip.mp4", handle, "video/mp4")},
                data={"backend": "quantum"},
            )
        assert response.status_code == 422
        assert "backend" in response.json()["detail"]

    def test_partial_line_coords_is_422(
        self, client: TestClient, api_env: ApiEnv, tmp_path: Path
    ) -> None:
        rid = api_env.ids["rest_a"]
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"x")
        with video.open("rb") as handle:
            response = client.post(
                f"/api/restaurants/{rid}/analysis",
                files={"file": ("clip.mp4", handle, "video/mp4")},
                data={"backend": "synthetic", "x1": "0.4"},
            )
        assert response.status_code == 422

    def test_non_http_url_is_422(self, client: TestClient, api_env: ApiEnv) -> None:
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/analysis", data={"url": "ftp://example.com/x.mp4"}
        )
        assert response.status_code == 422


class TestUrlAnalysis:
    def test_missing_yt_dlp_yields_actionable_422(
        self, client: TestClient, api_env: ApiEnv, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from toskana.api.routes import analysis as analysis_routes

        monkeypatch.setattr(analysis_routes, "_yt_dlp_available", lambda: False)
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/analysis",
            data={"url": "https://example.com/watch?v=x"},
        )
        assert response.status_code == 422
        assert "pip install yt-dlp" in response.json()["detail"]

    def test_unreachable_url_ends_in_error_state(self, client: TestClient, api_env: ApiEnv) -> None:
        pytest.importorskip("yt_dlp")
        rid = api_env.ids["rest_a"]
        response = client.post(
            f"/api/restaurants/{rid}/analysis",
            # Nothing listens on the discard port -> instant refusal, no network.
            data={"url": "http://127.0.0.1:9/not-a-video", "backend": "synthetic"},
        )
        assert response.status_code == 201
        job_id = response.json()["id"]

        def fetch() -> dict[str, Any]:
            return client.get(f"/api/restaurants/{rid}/analysis/{job_id}").json()

        job = _poll(fetch, lambda j: j["status"] in ("done", "error"), timeout_s=90.0)
        assert job["status"] == "error"
        assert "download failed" in job["error"]
        assert job["camera_id"] is None  # no camera row for a failed download
