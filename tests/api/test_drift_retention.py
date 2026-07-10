"""M10 monitoring API: the drift calibrate endpoint, ``/ws/live`` drift
messages and the manual snapshot-retention run endpoint."""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np
from fastapi.testclient import TestClient
from sqlalchemy import select

from tests.api.conftest import ApiEnv, add_event

# Reused fixture: an app with a live (synthetic-backend) pipeline.
from tests.api.test_stream_system import LiveEnv, live_env  # noqa: F401
from toskana.db.models import Event
from toskana.events.bus import TOPIC_DRIFT
from toskana.vision.drift import DriftDetector

_DAY_MS = 86_400_000


class TestCalibrateEndpoint:
    def test_unknown_camera_404(self, client: TestClient) -> None:
        assert client.post("/api/cameras/999999/calibrate").status_code == 404

    def test_camera_without_live_frame_409(self, client: TestClient, api_env: ApiEnv) -> None:
        # start_pipelines=False: the camera exists but has never produced a frame.
        response = client.post(f"/api/cameras/{api_env.ids['cam_a1']}/calibrate")
        assert response.status_code == 409
        assert "no live frame" in response.json()["detail"]

    def test_calibrate_running_camera_sets_reference(self, live_env: LiveEnv) -> None:  # noqa: F811
        cam = live_env.camera_id
        config = live_env.app.state.config
        reference = Path(config.snapshots_dir) / "calibration" / f"{cam}.png"
        with TestClient(live_env.app) as client:
            client.get(f"/api/snapshot/{cam}")  # blocks until the first frame exists
            response = client.post(f"/api/cameras/{cam}/calibrate")
            assert response.status_code == 200
            body = response.json()
            assert body["camera_id"] == cam
            assert body["running"] is True
            assert body["drift_ok"] is True  # calibrated, alarm reset
        assert reference.is_file() and reference.stat().st_size > 0

    def test_drift_status_surfaces_in_camera_status_and_health(
        self,
        live_env: LiveEnv,  # noqa: F811
    ) -> None:
        cam = live_env.camera_id
        with TestClient(live_env.app) as client:
            client.get(f"/api/snapshot/{cam}")  # first frame auto-calibrates
            status = client.get(f"/api/cameras/{cam}/status").json()
            assert status["drift_ok"] is True
            health = client.get("/api/system/health").json()
            by_camera = {row["camera_id"]: row for row in health["pipelines"]}
            assert by_camera[cam]["drift_ok"] is True


class TestDriftWs:
    def test_drift_payload_is_forwarded(self, client: TestClient, api_env: ApiEnv) -> None:
        bus = api_env.app.state.bus
        payload = {
            "restaurant_id": api_env.ids["rest_a"],
            "camera_id": api_env.ids["cam_a1"],
            "score": 0.31,
            "drift_ok": False,
            "ts": int(time.time() * 1000),
            "internal_extra": "dropped",
        }
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"] == "hello"
            bus.publish(TOPIC_DRIFT, payload)
            message = ws.receive_json()
        assert message["type"] == "drift"
        assert message["camera_id"] == api_env.ids["cam_a1"]
        assert message["score"] == 0.31
        assert message["drift_ok"] is False
        assert "internal_extra" not in message

    def test_detector_alarm_reaches_ws_client(
        self, client: TestClient, api_env: ApiEnv, tmp_path: Path
    ) -> None:
        """End to end: DriftDetector -> app bus -> {type: 'drift'} on /ws/live."""
        detector = DriftDetector(
            api_env.ids["cam_a1"],
            tmp_path,
            restaurant_id=api_env.ids["rest_a"],
            bus=api_env.app.state.bus,
            consecutive_required=1,
            check_interval_frames=1,
        )
        detector.calibrate(np.full((360, 640, 3), 230, np.uint8))
        with client.websocket_connect("/ws/live") as ws:
            assert ws.receive_json()["type"] == "hello"
            score = detector.observe(np.zeros((360, 640, 3), np.uint8))  # blocked view
            assert score is not None and score < detector.threshold
            drift_msg = ws.receive_json()
            gap_msg = ws.receive_json()  # the suspect-data marker rides along
        assert drift_msg["type"] == "drift"
        assert drift_msg["camera_id"] == api_env.ids["cam_a1"]
        assert drift_msg["drift_ok"] is False
        assert drift_msg["score"] == round(score, 4)
        assert gap_msg["type"] == "gap"
        assert gap_msg["gap"]["reason"] == "drift_alarm"


class TestRetentionRun:
    def test_expired_snapshot_deleted_recent_kept(
        self, client: TestClient, api_env: ApiEnv
    ) -> None:
        base = api_env.snapshots_dir
        old_rel, new_rel = "resta/2020-01-01/old.jpg", "resta/2026-01-01/new.jpg"
        for rel in (old_rel, new_rel):
            path = base / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"\xff\xd8jpeg")

        now = int(time.time() * 1000)
        retention_days = api_env.config.snapshot_retention_days
        with api_env.session() as session:
            old_id = add_event(
                session,
                restaurant_id=api_env.ids["rest_a"],
                camera_id=api_env.ids["cam_a1"],
                ts=now - (retention_days + 10) * _DAY_MS,
                snapshot_path=old_rel,
            )
            new_id = add_event(
                session,
                restaurant_id=api_env.ids["rest_a"],
                camera_id=api_env.ids["cam_a1"],
                ts=now,
                snapshot_path=new_rel,
            )
            session.commit()

        response = client.post("/api/system/retention/run")
        assert response.status_code == 200
        body = response.json()
        assert body["snapshot_retention_days"] == retention_days
        assert body["deleted_snapshots"] == 1
        assert body["cleared_events"] == 1
        assert body["runs"] >= 1  # this pass (+ the scheduled startup pass)

        assert not (base / old_rel).exists()
        assert (base / new_rel).is_file()  # inside the retention window: kept
        with api_env.session() as session:
            rows = {
                row.id: row
                for row in session.scalars(select(Event).where(Event.id.in_([old_id, new_id])))
            }
        assert rows[old_id].snapshot_path is None  # metadata row stays, image gone
        assert rows[new_id].snapshot_path == new_rel

    def test_second_run_is_idempotent(self, client: TestClient) -> None:
        first = client.post("/api/system/retention/run").json()
        second = client.post("/api/system/retention/run").json()
        assert second["deleted_snapshots"] == 0
        assert second["cleared_events"] == 0
        assert second["runs"] == first["runs"] + 1
