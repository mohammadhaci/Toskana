"""AlertNotifier: bus-driven webhook POSTs for camera_down/drift alerts,
manual stops stay silent, delivery failures are logged and never raised."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from toskana import alerts as alerts_module
from toskana.alerts import AlertNotifier
from toskana.db.base import Base, make_engine, make_session_factory
from toskana.db.models import Camera, Restaurant
from toskana.events.bus import TOPIC_DRIFT, TOPIC_GAP, EventBus
from toskana.events.writer import make_writer_session_factory


class _CapturingUrlopen:
    """Stand-in for urllib.request.urlopen recording every request."""

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.requests: list[dict[str, Any]] = []

    def __call__(self, request: Any, timeout: float | None = None) -> Any:
        if self.fail:
            raise OSError("connection refused")
        self.requests.append(
            {
                "url": request.full_url,
                "method": request.get_method(),
                "content_type": request.get_header("Content-type"),
                "body": json.loads(request.data.decode("utf-8")),
                "timeout": timeout,
            }
        )

        class _Response:
            def __enter__(self) -> _Response:
                return self

            def __exit__(self, *exc_info: object) -> None:
                return None

        return _Response()


@pytest.fixture()
def capture(monkeypatch: pytest.MonkeyPatch) -> _CapturingUrlopen:
    capturing = _CapturingUrlopen()
    monkeypatch.setattr(alerts_module.urllib.request, "urlopen", capturing)
    return capturing


@pytest.fixture()
def camera_env(tmp_path: Path) -> tuple[sessionmaker[Session], int]:
    """A seeded camera + a cross-thread-safe session factory (as in the app)."""
    db_path = str(tmp_path / "alerts.db")
    engine = make_engine(db_path)
    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        restaurant = Restaurant(slug="alerts", name="Alerts")
        session.add(restaurant)
        session.flush()
        camera = Camera(
            restaurant_id=restaurant.id,
            name="Pass links",
            source_type="file",
            source_url="./x.mp4",
        )
        session.add(camera)
        session.commit()
        camera_id = camera.id
    engine.dispose()
    return make_writer_session_factory(db_path), camera_id


def _run_notifier(
    bus: EventBus, notifier: AlertNotifier, publishes: list[tuple[str, dict]]
) -> None:
    """start -> publish -> stop (stop drains the queue, so delivery is done)."""
    notifier.start()
    try:
        for topic, payload in publishes:
            bus.publish(topic, payload)
    finally:
        notifier.stop()


class TestAlertNotifier:
    def test_drift_alarm_and_recovery_post_json(
        self, capture: _CapturingUrlopen, camera_env: tuple[sessionmaker[Session], int]
    ) -> None:
        session_factory, camera_id = camera_env
        bus = EventBus()
        notifier = AlertNotifier(
            "http://127.0.0.1:9/hook", bus=bus, session_factory=session_factory
        )
        alarm = {"camera_id": camera_id, "score": 0.31, "drift_ok": False, "ts": 1_000}
        recovered = {"camera_id": camera_id, "score": 0.97, "drift_ok": True, "ts": 2_000}
        _run_notifier(bus, notifier, [(TOPIC_DRIFT, alarm), (TOPIC_DRIFT, recovered)])

        assert notifier.sent == 2 and notifier.failed == 0
        first, second = capture.requests
        assert first["url"] == "http://127.0.0.1:9/hook"
        assert first["method"] == "POST"
        assert first["content_type"] == "application/json"
        assert first["timeout"] == pytest.approx(5.0)
        assert first["body"] == {
            "type": "drift",
            "camera_id": camera_id,
            "name": "Pass links",
            "ts": 1_000,
            "detail": "SSIM score 0.31",
        }
        assert second["body"]["type"] == "camera_recovered"
        assert second["body"]["ts"] == 2_000

    def test_unexpected_stop_alerts_manual_stop_does_not(
        self, capture: _CapturingUrlopen, camera_env: tuple[sessionmaker[Session], int]
    ) -> None:
        session_factory, camera_id = camera_env
        bus = EventBus()
        notifier = AlertNotifier(
            "http://127.0.0.1:9/hook", bus=bus, session_factory=session_factory
        )
        gap = {"restaurant_id": 1, "camera_id": camera_id, "from_ts": 5_000, "to_ts": 5_000}
        _run_notifier(
            bus,
            notifier,
            [
                (TOPIC_GAP, {**gap, "reason": "pipeline_start"}),  # never alerts
                (TOPIC_GAP, {**gap, "reason": "pipeline_stop"}),  # manual stop: silent
                (TOPIC_GAP, {**gap, "reason": "capture_stall"}),  # in-stream gap: silent
                (TOPIC_GAP, {**gap, "reason": "pipeline_error", "detail": "OSError: boom"}),
                (TOPIC_GAP, {**gap, "reason": "pipeline_ended"}),
            ],
        )
        assert [request["body"]["type"] for request in capture.requests] == [
            "camera_down",
            "camera_down",
        ]
        crashed, ended = (request["body"] for request in capture.requests)
        assert crashed["detail"] == "OSError: boom"
        assert crashed["ts"] == 5_000
        assert crashed["name"] == "Pass links"
        assert ended["detail"] == "pipeline_ended"

    def test_delivery_failure_is_logged_not_raised(
        self,
        monkeypatch: pytest.MonkeyPatch,
        camera_env: tuple[sessionmaker[Session], int],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        failing = _CapturingUrlopen(fail=True)
        monkeypatch.setattr(alerts_module.urllib.request, "urlopen", failing)
        session_factory, camera_id = camera_env
        bus = EventBus()
        notifier = AlertNotifier(
            "http://127.0.0.1:9/hook", bus=bus, session_factory=session_factory
        )
        with caplog.at_level("ERROR", logger="toskana.alerts"):
            _run_notifier(
                bus,
                notifier,
                [(TOPIC_DRIFT, {"camera_id": camera_id, "drift_ok": False, "ts": 1})],
            )
        assert notifier.failed == 1 and notifier.sent == 0
        assert "alert webhook POST failed" in caplog.text

    def test_unknown_camera_name_is_null(self, capture: _CapturingUrlopen) -> None:
        bus = EventBus()
        notifier = AlertNotifier("http://127.0.0.1:9/hook", bus=bus)  # no session factory
        _run_notifier(bus, notifier, [(TOPIC_DRIFT, {"camera_id": 42, "drift_ok": False, "ts": 1})])
        assert capture.requests[0]["body"]["name"] is None


class TestManagerCrashAlert:
    def test_pipeline_crash_publishes_error_marker_and_alert(
        self, capture: _CapturingUrlopen, tmp_path: Path
    ) -> None:
        """End to end: a crashing pipeline (missing source file) makes the
        manager publish a ``pipeline_error`` gap marker with the error detail,
        which the AlertNotifier turns into a camera_down webhook POST."""
        import time

        from toskana.config import AppConfig
        from toskana.vision.manager import PipelineManager

        db_path = str(tmp_path / "crash.db")
        engine = make_engine(db_path)
        Base.metadata.create_all(engine)
        with make_session_factory(engine)() as session:
            restaurant = Restaurant(slug="crash", name="Crash")
            session.add(restaurant)
            session.flush()
            camera = Camera(
                restaurant_id=restaurant.id,
                name="Broken cam",
                source_type="file",
                source_url=str(tmp_path / "missing.mp4"),
            )
            session.add(camera)
            session.commit()
            camera_id = camera.id
        engine.dispose()

        config = AppConfig(
            active_restaurant_slug="crash",
            db_path=db_path,
            snapshots_dir=str(tmp_path / "snapshots"),
            detector_backend="synthetic",
        )
        session_factory = make_writer_session_factory(db_path)
        bus = EventBus()
        gaps: list[dict] = []
        bus.subscribe(TOPIC_GAP, gaps.append)
        notifier = AlertNotifier(
            "http://127.0.0.1:9/hook", bus=bus, session_factory=session_factory
        )
        notifier.start()
        manager = PipelineManager(config, bus=bus, session_factory=session_factory)
        try:
            manager.start_camera(camera_id)
            deadline = time.monotonic() + 5.0
            while manager.is_running(camera_id):
                assert time.monotonic() < deadline, "crashing pipeline never ended"
                time.sleep(0.01)
        finally:
            manager.stop_all()
            notifier.stop()

        reasons = [gap["reason"] for gap in gaps]
        assert reasons == ["pipeline_start", "pipeline_error"]
        error_gap = gaps[1]
        assert "SourceOpenError" in error_gap["detail"]
        status = manager.camera_status(camera_id)
        assert status is not None and "SourceOpenError" in (status["last_error"] or "")

        (alert,) = (request["body"] for request in capture.requests)
        assert alert["type"] == "camera_down"
        assert alert["camera_id"] == camera_id
        assert alert["name"] == "Broken cam"
        assert "SourceOpenError" in alert["detail"]
