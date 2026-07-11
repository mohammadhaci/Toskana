"""Fire-and-forget webhook alerts for camera outages and drift.

When ``alert_webhook_url`` is configured the :class:`AlertNotifier`
subscribes to the existing bus topics (no pipeline entanglement) and POSTs
one JSON document per alert::

    {"type": "camera_down" | "drift" | "camera_recovered",
     "camera_id": 3, "name": "Pass links", "ts": 1751000000000,
     "detail": "..."}

Triggers:

* ``gap`` markers with reason ``pipeline_error`` (pipeline crashed) or
  ``pipeline_ended`` (source ended without a stop request) -> ``camera_down``.
  Manual stops (``pipeline_stop``) never alert.
* ``drift`` bus events: alarm raised -> ``drift``, alarm cleared ->
  ``camera_recovered``.

Delivery happens on a dedicated daemon thread via ``urllib`` (no extra
dependency), with a 5 s timeout; failures are logged, never raised.
``stop()`` drains the queue so tests and graceful shutdowns see every
accepted alert.
"""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import urllib.request
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from toskana.db.models import Camera
from toskana.events.bus import TOPIC_DRIFT, TOPIC_GAP, EventBus

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 5.0

#: gap reasons that mean the camera went down without being asked to.
_DOWN_REASONS = frozenset({"pipeline_error", "pipeline_ended"})

_STOP = object()


class AlertNotifier:
    """POSTs camera_down/drift/camera_recovered alerts to a webhook URL."""

    def __init__(
        self,
        webhook_url: str,
        *,
        bus: EventBus,
        session_factory: sessionmaker[Session] | None = None,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self._webhook_url = webhook_url
        self._bus = bus
        self._session_factory = session_factory
        self._timeout_s = timeout_s
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._unsubscribes: list[Any] = []
        self._camera_names: dict[int, str | None] = {}
        self.sent = 0
        self.failed = 0

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("AlertNotifier already started")
        self._thread = threading.Thread(target=self._run, name="alert-notifier", daemon=True)
        self._thread.start()
        self._unsubscribes.append(self._bus.subscribe(TOPIC_GAP, self._on_gap))
        self._unsubscribes.append(self._bus.subscribe(TOPIC_DRIFT, self._on_drift))

    def stop(self) -> None:
        """Unsubscribe, drain the queue, deliver what is pending. Idempotent."""
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None

    # -- bus callbacks (pipeline threads: enqueue only) ------------------------

    def _on_gap(self, payload: dict[str, Any]) -> None:
        reason = payload.get("reason")
        if reason not in _DOWN_REASONS:
            return
        self._enqueue(
            "camera_down",
            camera_id=payload.get("camera_id"),
            ts=payload.get("from_ts"),
            detail=payload.get("detail") or str(reason),
        )

    def _on_drift(self, payload: dict[str, Any]) -> None:
        alarmed = payload.get("drift_ok") is False
        score = payload.get("score")
        self._enqueue(
            "drift" if alarmed else "camera_recovered",
            camera_id=payload.get("camera_id"),
            ts=payload.get("ts"),
            detail=f"SSIM score {score}" if score is not None else "drift check",
        )

    def _enqueue(self, alert_type: str, *, camera_id: Any, ts: Any, detail: str) -> None:
        self._queue.put(
            {
                "type": alert_type,
                "camera_id": camera_id,
                "ts": ts if ts is not None else int(time.time() * 1000),
                "detail": detail,
            }
        )

    # -- delivery thread ---------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            item["name"] = self._camera_name(item.get("camera_id"))
            self._deliver(item)

    def _camera_name(self, camera_id: Any) -> str | None:
        if not isinstance(camera_id, int) or self._session_factory is None:
            return None
        if camera_id not in self._camera_names:
            try:
                with self._session_factory() as session:
                    self._camera_names[camera_id] = session.scalar(
                        select(Camera.name).where(Camera.id == camera_id)
                    )
            except Exception:  # noqa: BLE001 - a DB hiccup must not kill alerting
                logger.exception("alert notifier: camera %s name lookup failed", camera_id)
                return None
        return self._camera_names[camera_id]

    def _deliver(self, alert: dict[str, Any]) -> None:
        request = urllib.request.Request(
            self._webhook_url,
            data=json.dumps(alert, sort_keys=True).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_s):
                pass
            self.sent += 1
        except Exception:  # noqa: BLE001 - alerting must never take the app down
            self.failed += 1
            logger.exception(
                "alert webhook POST failed (%s alert for camera %s)",
                alert.get("type"),
                alert.get("camera_id"),
            )
