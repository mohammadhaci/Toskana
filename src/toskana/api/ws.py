"""``/ws/live`` — live counters and event feed for the dashboard.

On connect the client receives ``{type: "hello", counters: [...]}`` with
today's per-category out/in/net totals (canonical events, restaurant-local
day). Afterwards every bus ``crossing`` is forwarded as ``{type:
"crossing", event: {...}}`` and every ``gap`` as ``{type: "gap", gap:
{...}}``.

M8 (cross-camera dedup): when the DedupEngine demotes an optimistically
canonical event it publishes on the ``dedup_correction`` bus topic, which
this module forwards as a third message type — ``{type: "correction",
event_id, dedup_group_id, is_canonical: false, category_id, menu_item_id,
direction, camera_id, ts}`` — so live counters can decrement the affected
category/direction without a reload.

AI Event Refiner: when the vision LLM verifies/corrects an event the
RefinerEngine publishes on the ``refiner_correction`` bus topic, forwarded
here as ``{type: "refined", event_id, category_id, previous_category_id,
menu_item_id, refiner_note, ...}``.

Bus callbacks fire on pipeline threads; they are marshalled into the event
loop with ``call_soon_threadsafe`` and fanned out to one ``asyncio.Queue``
per connected client, so a slow client never blocks the pipelines.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from toskana.api.timeutils import local_day_bounds, local_today
from toskana.db.models import Category, Event, Restaurant
from toskana.events.bus import (
    TOPIC_CORRECTION,
    TOPIC_CROSSING,
    TOPIC_DRIFT,
    TOPIC_GAP,
    TOPIC_REFINED,
    EventBus,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_QUEUE = 500

#: crossing payload keys forwarded to WS clients (drop internal extras).
_CROSSING_KEYS = (
    "id",
    "restaurant_id",
    "camera_id",
    "line_id",
    "track_id",
    "category_id",
    "menu_item_id",
    "menu_item_name",
    "raw_class_name",
    "class_name",
    "confidence",
    "direction",
    "ts",
    "frame_index",
    "anchor_x",
    "anchor_y",
    "snapshot_path",
)

_GAP_KEYS = ("restaurant_id", "camera_id", "from_ts", "to_ts", "reason")

#: drift payload keys forwarded to WS clients.
_DRIFT_KEYS = ("restaurant_id", "camera_id", "score", "drift_ok", "ts")

#: dedup_correction payload keys forwarded to WS clients.
_CORRECTION_KEYS = (
    "event_id",
    "dedup_group_id",
    "is_canonical",
    "restaurant_id",
    "camera_id",
    "category_id",
    "menu_item_id",
    "direction",
    "ts",
)

#: refiner_correction payload keys forwarded to WS clients.
_REFINED_KEYS = (
    "event_id",
    "restaurant_id",
    "camera_id",
    "category_id",
    "previous_category_id",
    "menu_item_id",
    "direction",
    "ts",
    "is_item",
    "refined",
    "refiner_note",
)


class LiveBroadcaster:
    """Fans bus events out to all connected ``/ws/live`` clients."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._clients: set[asyncio.Queue[dict[str, Any]]] = set()
        self._clients_lock = threading.Lock()
        self._unsubscribes: list[Any] = []

    # -- lifecycle (called from the lifespan, inside the event loop) ----------

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop
        self._unsubscribes.append(self._bus.subscribe(TOPIC_CROSSING, self._on_crossing))
        self._unsubscribes.append(self._bus.subscribe(TOPIC_GAP, self._on_gap))
        self._unsubscribes.append(self._bus.subscribe(TOPIC_CORRECTION, self._on_correction))
        self._unsubscribes.append(self._bus.subscribe(TOPIC_REFINED, self._on_refined))
        self._unsubscribes.append(self._bus.subscribe(TOPIC_DRIFT, self._on_drift))

    def stop(self) -> None:
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        self._loop = None

    # -- client registry --------------------------------------------------------

    def register(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=_MAX_QUEUE)
        with self._clients_lock:
            self._clients.add(queue)
        return queue

    def unregister(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        with self._clients_lock:
            self._clients.discard(queue)

    @property
    def client_count(self) -> int:
        with self._clients_lock:
            return len(self._clients)

    # -- bus callbacks (pipeline threads) -------------------------------------------

    def _on_crossing(self, payload: dict[str, Any]) -> None:
        event = {key: payload[key] for key in _CROSSING_KEYS if key in payload}
        self._dispatch({"type": "crossing", "event": event})

    def _on_gap(self, payload: dict[str, Any]) -> None:
        gap = {key: payload[key] for key in _GAP_KEYS if key in payload}
        self._dispatch({"type": "gap", "gap": gap})

    def _on_correction(self, payload: dict[str, Any]) -> None:
        """A dedup demotion: live counters must decrement this event."""
        correction = {key: payload[key] for key in _CORRECTION_KEYS if key in payload}
        self._dispatch({"type": "correction", **correction})

    def _on_refined(self, payload: dict[str, Any]) -> None:
        """The vision LLM verified/corrected an event (AI Event Refiner)."""
        refined = {key: payload[key] for key in _REFINED_KEYS if key in payload}
        self._dispatch({"type": "refined", **refined})

    def _on_drift(self, payload: dict[str, Any]) -> None:
        """A camera drift alarm was raised or cleared (M10 watchdog)."""
        drift = {key: payload[key] for key in _DRIFT_KEYS if key in payload}
        self._dispatch({"type": "drift", **drift})

    def _dispatch(self, message: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            loop.call_soon_threadsafe(self._fanout, message)
        except RuntimeError:  # loop shut down between the check and the call
            pass

    def _fanout(self, message: dict[str, Any]) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for queue in clients:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("ws client queue full; dropping live message")


def today_counters(session_factory: Any, restaurant_slug: str) -> list[dict[str, Any]]:
    """Today's per-category out/in/net (canonical only) for the hello message."""
    with session_factory() as session:
        restaurant = session.scalar(select(Restaurant).where(Restaurant.slug == restaurant_slug))
        if restaurant is None:
            return []
        day_from, day_to = local_day_bounds(restaurant.timezone, local_today(restaurant.timezone))
        per_category: dict[int | None, list[int]] = {}
        stmt = select(Event.category_id, Event.direction).where(
            Event.restaurant_id == restaurant.id,
            Event.is_canonical.is_(True),
            Event.ts >= day_from,
            Event.ts < day_to,
        )
        for category_id, direction in session.execute(stmt):
            pair = per_category.setdefault(category_id, [0, 0])
            pair[0 if direction == "out" else 1] += 1
        categories = session.scalars(
            select(Category)
            .where(Category.restaurant_id == restaurant.id)
            .order_by(Category.sort_order, Category.id)
        ).all()
    counters = []
    for category in categories:
        out, in_ = per_category.get(category.id, [0, 0])
        counters.append(
            {
                "category_id": category.id,
                "key": category.key,
                "name_de": category.name_de,
                "name_en": category.name_en,
                "color_hex": category.color_hex,
                "out": out,
                "in": in_,
                "net": out - in_,
            }
        )
    if None in per_category:
        out, in_ = per_category[None]
        counters.append(
            {
                "category_id": None,
                "key": None,
                "name_de": None,
                "name_en": None,
                "color_hex": None,
                "out": out,
                "in": in_,
                "net": out - in_,
            }
        )
    return counters


@router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    broadcaster: LiveBroadcaster = websocket.app.state.broadcaster
    config = websocket.app.state.config
    session_factory = websocket.app.state.session_factory
    await websocket.accept()

    counters = await asyncio.to_thread(
        today_counters, session_factory, config.active_restaurant_slug
    )
    queue = broadcaster.register()
    try:
        await websocket.send_json({"type": "hello", "counters": counters})

        async def pump() -> None:
            while True:
                message = await queue.get()
                await websocket.send_json(message)

        pump_task = asyncio.create_task(pump())
        try:
            while True:  # drain client messages; exit on disconnect
                await websocket.receive_text()
        finally:
            pump_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump_task
    except WebSocketDisconnect:
        pass
    finally:
        broadcaster.unregister(queue)
