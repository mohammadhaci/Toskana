"""End-to-end MJPEG/snapshot streaming around a real (synthetic-backend)
pipeline over a generated scenario video, plus the system endpoints."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tests.api.conftest import ApiEnv
from tests.tools.make_synthetic_video import generate_scenario
from toskana import __version__
from toskana.api.app import create_app
from toskana.config import AppConfig
from toskana.db.base import Base, make_engine, make_session_factory
from toskana.db.models import Camera, Category, Line, Restaurant

_BOUNDARY = b"--toskana-frame"
_JPEG_SOI = b"\xff\xd8"


@dataclass
class LiveEnv:
    app: Any
    restaurant_id: int
    camera_id: int


@pytest.fixture()
def live_env(tmp_path: Path) -> LiveEnv:
    """An app with ``start_pipelines=True`` over a looping single_drink video."""
    generated = generate_scenario("single_drink", tmp_path / "scenarios")
    video = generated.video_paths["cam"]

    db_path = str(tmp_path / "live.db")
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        restaurant = Restaurant(slug="livea", name="Live A", timezone="Europe/Vienna")
        session.add(restaurant)
        session.flush()
        session.add(
            Category(restaurant_id=restaurant.id, key="drink", name_de="Getränk", name_en="Drink")
        )
        camera = Camera(
            restaurant_id=restaurant.id,
            name="Live cam",
            source_type="file",
            source_url=str(video),
        )
        session.add(camera)
        session.flush()
        session.add(
            Line(restaurant_id=restaurant.id, camera_id=camera.id, x1=0.5, y1=0.0, x2=0.5, y2=1.0)
        )
        session.commit()
        restaurant_id, camera_id = restaurant.id, camera.id
    engine.dispose()

    snapshots_dir = tmp_path / "snapshots"
    snapshots_dir.mkdir()
    config = AppConfig(
        active_restaurant_slug="livea",
        db_path=db_path,
        snapshots_dir=str(snapshots_dir),
        detector_backend="synthetic",
        loop_file_sources=True,  # the 4s clip loops so the stream never runs dry
    )
    app = create_app(config, start_pipelines=True)
    return LiveEnv(app=app, restaurant_id=restaurant_id, camera_id=camera_id)


def _pipeline_threads() -> list[str]:
    return [
        thread.name
        for thread in threading.enumerate()
        if thread.is_alive()
        and (thread.name.startswith("manager-cam") or thread.name == "event-writer")
    ]


class TestStreamEndToEnd:
    def test_snapshot_stream_and_system_with_live_pipeline(self, live_env: LiveEnv) -> None:
        cam = live_env.camera_id
        with TestClient(live_env.app) as client:
            # -- snapshot: one annotated JPEG (endpoint waits for the first frame)
            snapshot = client.get(f"/api/snapshot/{cam}")
            assert snapshot.status_code == 200
            assert snapshot.headers["content-type"] == "image/jpeg"
            assert snapshot.content.startswith(_JPEG_SOI)

            # -- unknown camera 404s even with pipelines running
            assert client.get("/api/snapshot/999999").status_code == 404
            assert client.get("/api/stream/999999").status_code == 404

            # -- system health: ok db + a running pipeline block for the camera
            health = client.get("/api/system/health").json()
            assert health["status"] == "ok"
            assert health["db_ok"] is True
            by_camera = {row["camera_id"]: row for row in health["pipelines"]}
            assert by_camera[cam]["running"] is True
            assert by_camera[cam]["backend"] == "synthetic"
            assert by_camera[cam]["frames"] > 0

            # -- system info: version, device info, active restaurant
            info = client.get("/api/system/info").json()
            assert info["version"] == __version__
            assert info["active_restaurant_slug"] == "livea"
            assert info["active_restaurant"]["id"] == live_env.restaurant_id
            assert info["detector_backend"] == "synthetic"
            assert info["device"] == "auto"
            assert isinstance(info["torch_available"], bool)
            assert isinstance(info["cuda_available"], bool)
            assert info["db_size_bytes"] > 0

            # -- MJPEG stream. Starlette's TestClient buffers a response fully
            # before handing it back, so an unbounded stream would never
            # return; ?max_frames bounds it server-side (the browser dashboard
            # uses the unbounded default).
            response = client.get(f"/api/stream/{cam}", params={"max_frames": 3})
            assert response.status_code == 200
            assert response.headers["content-type"].startswith(
                "multipart/x-mixed-replace; boundary=toskana-frame"
            )
            buffer = response.content
            assert buffer.count(_BOUNDARY) >= 2
            assert buffer.count(_JPEG_SOI) >= 2
            assert b"Content-Type: image/jpeg" in buffer

        # Lifespan shutdown must join the pipeline + writer threads cleanly.
        assert _pipeline_threads() == []


class TestSystemWithoutPipelines:
    def test_health_and_info(self, client: TestClient, api_env: ApiEnv) -> None:
        health = client.get("/api/system/health").json()
        assert health["status"] == "ok"
        assert health["db_ok"] is True
        assert health["pipelines"] == []  # start_pipelines=False
        assert health["writer_written_events"] == 0

        info = client.get("/api/system/info").json()
        assert info["version"] == __version__
        assert info["active_restaurant_slug"] == "resta"
        assert info["active_restaurant"]["slug"] == "resta"
        assert info["snapshots_dir"] == str(api_env.snapshots_dir)
