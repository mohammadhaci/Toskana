"""Tiny thread-safe in-process pub/sub bus.

Delivery is synchronous in the publisher's thread; long-running subscribers
should enqueue internally (as :class:`~toskana.events.writer.EventWriter`
does). Subscriber exceptions are logged and never break other subscribers
or the publisher.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

Subscriber = Callable[[Any], None]

TOPIC_CROSSING = "crossing"
TOPIC_GAP = "gap"
#: DedupEngine -> EventWriter: apply an ``is_canonical``/``dedup_group_id``
#: UPDATE to an already-inserted event (single-writer discipline).
TOPIC_DEMOTE = "demote"
#: DedupEngine -> /ws/live: an optimistically-canonical event was demoted;
#: forwarded to clients as ``{type: "correction", ...}``.
TOPIC_CORRECTION = "dedup_correction"
#: DriftDetector -> /ws/live + monitoring: a camera drift alarm was raised
#: (or cleared); forwarded to clients as ``{type: "drift", ...}``.
TOPIC_DRIFT = "drift"


class EventBus:
    """Topic-based publish/subscribe, safe to use from multiple threads."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: dict[str, list[Subscriber]] = {}

    def subscribe(self, topic: str, fn: Subscriber) -> Callable[[], None]:
        """Register ``fn`` for ``topic``; returns an unsubscribe callable."""
        with self._lock:
            self._subscribers.setdefault(topic, []).append(fn)
        return lambda: self.unsubscribe(topic, fn)

    def unsubscribe(self, topic: str, fn: Subscriber) -> None:
        """Remove ``fn`` from ``topic`` (no-op if not subscribed)."""
        with self._lock:
            subscribers = self._subscribers.get(topic)
            if subscribers is not None and fn in subscribers:
                subscribers.remove(fn)

    def publish(self, topic: str, payload: Any) -> int:
        """Deliver ``payload`` to all current subscribers; returns their count."""
        with self._lock:
            subscribers = list(self._subscribers.get(topic, ()))
        for fn in subscribers:
            try:
                fn(payload)
            except Exception:  # noqa: BLE001 - one bad subscriber must not break others
                logger.exception("event bus subscriber %r failed on topic %r", fn, topic)
        return len(subscribers)
