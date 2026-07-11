"""AI Event Refiner: verify/correct counted events with a vision LLM.

The :class:`RefinerEngine` subscribes to the ``crossing`` bus topic (like
the M8 DedupEngine) and processes events on one worker thread:

1. skip unless the refiner provider is enabled; skip events whose detector
   confidence is already >= ``only_below_confidence`` (1.0 default
   = refine everything; lowering it to e.g. 0.65 cuts LLM cost by only
   double-checking uncertain detections). Settings come from the dashboard
   (``app_settings`` row, see :mod:`toskana.settings_store`) with the
   ``refiner_*`` config fields as fallback, and can be swapped at runtime
   via :meth:`RefinerEngine.reconfigure`;
2. load the event's snapshot JPEG, crop it to the item's bounding box
   (``bbox_px`` in the payload, expanded by ~15 %) and re-encode;
3. call the configured :class:`~toskana.refiner.RefinerBackend` with the
   restaurant's categories (+ menu items when ``refiner_match_menu_items``);
4. apply the verdict to the ``events`` row — ``refined=True`` +
   ``refiner_note`` always; ``category_id`` only when the LLM names a known,
   different category; ``menu_item_id`` on a case-insensitive menu match.
   Counts are never deleted and ``raw_class_name`` stays untouched;
5. publish a ``refiner_correction`` bus message (forwarded by ``/ws/live``
   as ``{type: "refined", ...}``) so live counters/pages update.

Calls are rate-limited (``refiner_max_per_minute``); backend failures are
counted and logged, never raised — the LLM is an optional adviser, not a
dependency of the counting pipeline. Writes use their own thread-safe
session factory (NullPool, like the EventWriter's).
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import cv2
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.models import Category, Event, MenuItem
from toskana.events.bus import TOPIC_CROSSING, TOPIC_REFINED, EventBus
from toskana.refiner import (
    AnthropicBackend,
    OpenAICompatBackend,
    RefinerBackend,
    RefinerCategory,
    RefinerError,
    RefinerResult,
)
from toskana.settings_store import (
    RefinerSettings,
    effective_refiner_settings,
    refiner_settings_from_config,
)

logger = logging.getLogger(__name__)

_STOP = object()

#: bounding-box crop margin (fraction of box width/height on each side).
CROP_MARGIN = 0.15
_JPEG_QUALITY = 85
_MAX_QUEUE = 200
_CATALOG_TTL_S = 60.0
#: the EventWriter batches inserts (~0.25 s); wait briefly for the row.
_ROW_WAIT_ATTEMPTS = 10
_ROW_WAIT_S = 0.15

PROVIDER_OFF = "off"
PROVIDER_ANTHROPIC = "anthropic"
PROVIDER_OPENAI_COMPAT = "openai_compatible"


def build_backend_from_settings(settings: RefinerSettings) -> RefinerBackend | None:
    """The backend instance for the given settings, or None when off.

    ``ANTHROPIC_API_KEY`` in the environment stays the final fallback for
    the Anthropic API key when no key is stored/configured.
    """
    if settings.provider == PROVIDER_ANTHROPIC:
        import os

        api_key = settings.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            logger.warning(
                "refiner provider=anthropic but no API key configured "
                "(set it in the dashboard, refiner_api_key or ANTHROPIC_API_KEY); "
                "refiner disabled"
            )
            return None
        return AnthropicBackend(settings.model, api_key=api_key)
    if settings.provider == PROVIDER_OPENAI_COMPAT:
        return OpenAICompatBackend(
            settings.model,
            base_url=settings.base_url,
            api_key=settings.api_key,
        )
    return None


def build_backend(config: AppConfig) -> RefinerBackend | None:
    """The config.yaml-configured backend instance, or None when off."""
    return build_backend_from_settings(refiner_settings_from_config(config))


@dataclass
class RefinerStats:
    """Snapshot of the engine's counters for ``/api/system/health``."""

    provider: str
    model: str | None
    enabled: bool
    queue_size: int
    refined: int
    failures: int
    skipped: int
    last_error: str | None


@dataclass
class _Catalog:
    """Per-restaurant category/menu lookup tables (cached ~60 s)."""

    categories: list[RefinerCategory] = field(default_factory=list)
    category_id_by_key: dict[str, int] = field(default_factory=dict)
    category_key_by_id: dict[int, str] = field(default_factory=dict)
    menu_names: list[str] = field(default_factory=list)
    menu_id_by_lower_name: dict[str, int] = field(default_factory=dict)


class RefinerEngine:
    """One worker thread refining crossing events through a vision LLM."""

    def __init__(
        self,
        config: AppConfig,
        *,
        bus: EventBus,
        session_factory: sessionmaker[Session],
        backend: RefinerBackend | None = None,
        settings: RefinerSettings | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._session_factory = session_factory
        # Effective settings: dashboard row (app_settings) overrides config.yaml.
        if settings is None:
            settings = effective_refiner_settings(session_factory, config)
        self._settings = settings
        self._backend = backend if backend is not None else build_backend_from_settings(settings)
        self._snapshots_dir = Path(config.snapshots_dir)
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=_MAX_QUEUE)
        self._thread: threading.Thread | None = None
        self._unsubscribes: list[Any] = []
        self._stop_event = threading.Event()
        self._min_interval_s = self._interval_s(self._settings)
        self._next_call_at = 0.0
        self._catalog_cache: dict[int, tuple[float, _Catalog]] = {}
        self._lock = threading.Lock()
        self._refined = 0
        self._failures = 0
        self._skipped = 0
        self._last_error: str | None = None

    @staticmethod
    def _interval_s(settings: RefinerSettings) -> float:
        return 60.0 / settings.max_per_minute if settings.max_per_minute > 0 else 0.0

    @property
    def enabled(self) -> bool:
        return self._settings.provider != PROVIDER_OFF and self._backend is not None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Subscribe + start the worker thread.

        The worker always runs (even with provider=off, where it idles) so
        a dashboard :meth:`reconfigure` can enable the refiner at runtime
        without a restart.
        """
        if self._thread is not None:
            raise RuntimeError("RefinerEngine already started")
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="refiner-engine", daemon=True)
        self._thread.start()
        self._unsubscribes.append(self._bus.subscribe(TOPIC_CROSSING, self._on_crossing))

    def stop(self) -> None:
        """Unsubscribe, drain the queue (without rate-limit pauses), join."""
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        if self._thread is None:
            return
        self._stop_event.set()  # skip rate-limit waits while draining
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None

    # -- runtime reconfiguration -----------------------------------------------

    def reconfigure(self, settings: RefinerSettings) -> None:
        """Apply new settings at runtime: swap the backend + thresholds/rate.

        Thread-safe; keeps the queue and counters, resets ``last_error``.
        The old backend is closed — a refine call in flight on the worker
        thread may fail once (counted, never raised, like any backend error).
        """
        new_backend = build_backend_from_settings(settings)
        with self._lock:
            old_backend = self._backend
            self._settings = settings
            self._backend = new_backend
            self._min_interval_s = self._interval_s(settings)
            self._next_call_at = 0.0
            self._last_error = None
        if old_backend is not None and old_backend is not new_backend:
            close = getattr(old_backend, "close", None)
            if callable(close):
                close()
        logger.info("refiner reconfigured: provider=%s model=%s", settings.provider, settings.model)

    # -- bus callback (pipeline threads: enqueue only) --------------------------

    def _on_crossing(self, payload: dict[str, Any]) -> None:
        try:
            self._queue.put_nowait(dict(payload))
        except queue.Full:
            with self._lock:
                self._skipped += 1
            logger.warning("refiner queue full; skipping event %s", payload.get("id"))

    # -- observability -----------------------------------------------------------

    @property
    def stats(self) -> RefinerStats:
        with self._lock:
            return RefinerStats(
                provider=self._settings.provider,
                model=self._backend.model if self._backend is not None else None,
                enabled=self.enabled,
                queue_size=self._queue.qsize(),
                refined=self._refined,
                failures=self._failures,
                skipped=self._skipped,
                last_error=self._last_error,
            )

    # -- worker thread -------------------------------------------------------------

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is _STOP:
                return
            try:
                self._process(item)
            except Exception as exc:  # noqa: BLE001 - the refiner must never crash the app
                self._count_failure(f"unexpected refiner error: {exc}")
                logger.exception("refiner failed on event %s", item.get("id"))

    def _process(self, payload: dict[str, Any]) -> None:
        settings = self._settings
        backend = self._backend
        if settings.provider == PROVIDER_OFF or backend is None:
            return  # idle worker: the refiner is (currently) off
        confidence = payload.get("confidence")
        if isinstance(confidence, (int, float)) and confidence >= settings.only_below_confidence:
            self._count_skip()
            return
        snapshot_path = payload.get("snapshot_path")
        if not snapshot_path:
            self._count_skip()
            return

        restaurant_id = payload.get("restaurant_id")
        if not isinstance(restaurant_id, int):
            self._count_skip()
            return
        catalog = self._catalog(restaurant_id)
        if not catalog.categories:
            self._count_skip()  # nothing to verify against
            return

        image_jpeg = self._load_image(str(snapshot_path), payload.get("bbox_px"))
        if image_jpeg is None:
            self._count_failure(f"snapshot unreadable: {snapshot_path}")
            return

        self._rate_limit()
        menu_items = catalog.menu_names if settings.match_menu_items else []
        try:
            result = backend.refine(image_jpeg, catalog.categories, menu_items)
        except RefinerError as exc:
            self._count_failure(str(exc))
            logger.warning("refiner backend failed for event %s: %s", payload.get("id"), exc)
            return

        self._apply(payload, result, catalog, backend)

    # -- image ----------------------------------------------------------------------

    def _load_image(self, snapshot_path: str, bbox: Any) -> bytes | None:
        """Read the event snapshot, crop to the item bbox (+margin), re-encode."""
        path = self._snapshots_dir / snapshot_path
        frame = cv2.imread(str(path))
        if frame is None:
            return None
        if isinstance(bbox, (tuple, list)) and len(bbox) == 4:
            height, width = frame.shape[:2]
            x1, y1, x2, y2 = (float(v) for v in bbox)
            margin_x = (x2 - x1) * CROP_MARGIN
            margin_y = (y2 - y1) * CROP_MARGIN
            left = max(0, int(x1 - margin_x))
            top = max(0, int(y1 - margin_y))
            right = min(width, int(round(x2 + margin_x)))
            bottom = min(height, int(round(y2 + margin_y)))
            if right - left >= 2 and bottom - top >= 2:
                frame = frame[top:bottom, left:right]
        ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
        if not ok:
            return None
        return bytes(buffer.tobytes())

    # -- catalog ---------------------------------------------------------------------

    def _catalog(self, restaurant_id: int) -> _Catalog:
        cached = self._catalog_cache.get(restaurant_id)
        now = time.monotonic()
        if cached is not None and now - cached[0] < _CATALOG_TTL_S:
            return cached[1]
        catalog = _Catalog()
        try:
            with self._session_factory() as session:
                categories = session.scalars(
                    select(Category)
                    .where(Category.restaurant_id == restaurant_id)
                    .order_by(Category.sort_order, Category.id)
                ).all()
                for category in categories:
                    name = category.name_en
                    if category.name_de and category.name_de != category.name_en:
                        name = f"{category.name_en} ({category.name_de})"
                    catalog.categories.append(RefinerCategory(key=category.key, name=name))
                    catalog.category_id_by_key[category.key] = category.id
                    catalog.category_key_by_id[category.id] = category.key
                items = session.scalars(
                    select(MenuItem).where(
                        MenuItem.restaurant_id == restaurant_id,
                        MenuItem.is_active.is_(True),
                    )
                ).all()
                for item in items:
                    catalog.menu_names.append(item.name)
                    catalog.menu_id_by_lower_name[item.name.lower()] = item.id
        except Exception:  # noqa: BLE001 - a DB hiccup must not kill the refiner
            logger.exception("refiner: catalog load failed for restaurant %s", restaurant_id)
        self._catalog_cache[restaurant_id] = (now, catalog)
        return catalog

    # -- rate limiting -----------------------------------------------------------------

    def _rate_limit(self) -> None:
        if self._min_interval_s <= 0 or self._stop_event.is_set():
            return
        wait = self._next_call_at - time.monotonic()
        if wait > 0:
            self._stop_event.wait(wait)
        self._next_call_at = time.monotonic() + self._min_interval_s

    # -- applying the verdict -------------------------------------------------------------

    def _apply(
        self,
        payload: dict[str, Any],
        result: RefinerResult,
        catalog: _Catalog,
        backend: RefinerBackend,
    ) -> None:
        event_id = payload.get("id")
        if not isinstance(event_id, str):
            self._count_failure("crossing payload without event id")
            return

        with self._session_factory() as session:
            event = self._wait_for_row(session, event_id)
            if event is None:
                self._count_failure(f"event row {event_id} never appeared")
                return

            previous_category_id = event.category_id
            original_key = (
                catalog.category_key_by_id.get(previous_category_id)
                if previous_category_id is not None
                else None
            )

            unknown_key = False
            if result.is_item and result.category_key is not None:
                new_category_id = catalog.category_id_by_key.get(result.category_key)
                if new_category_id is None:
                    unknown_key = True  # never touch counts on an unknown verdict
                elif new_category_id != event.category_id:
                    event.category_id = new_category_id
            if result.is_item and result.menu_item_name is not None:
                menu_item_id = catalog.menu_id_by_lower_name.get(result.menu_item_name.lower())
                if menu_item_id is not None:
                    event.menu_item_id = menu_item_id

            event.refined = True
            event.refiner_note = self._note(backend, result, original_key, unknown_key)
            session.commit()

            correction = {
                "event_id": event.id,
                "restaurant_id": event.restaurant_id,
                "camera_id": event.camera_id,
                "category_id": event.category_id,
                "previous_category_id": previous_category_id,
                "menu_item_id": event.menu_item_id,
                "direction": event.direction,
                "ts": event.ts,
                "is_item": result.is_item,
                "refined": True,
                "refiner_note": event.refiner_note,
            }

        with self._lock:
            self._refined += 1
        # Same pattern as the dedup demotion corrections: /ws/live forwards
        # this as {type: "refined", ...} so live counters/pages update.
        self._bus.publish(TOPIC_REFINED, correction)

    def _wait_for_row(self, session: Session, event_id: str) -> Event | None:
        """The EventWriter flushes in batches; wait briefly for the insert."""
        for _ in range(_ROW_WAIT_ATTEMPTS):
            event = session.get(Event, event_id)
            if event is not None:
                return event
            # Shorter waits while draining on stop, so shutdown stays fast.
            time.sleep(0.05 if self._stop_event.is_set() else _ROW_WAIT_S)
        return session.get(Event, event_id)

    @staticmethod
    def _note(
        backend: RefinerBackend,
        result: RefinerResult,
        original_key: str | None,
        unknown_key: bool,
    ) -> str:
        parts = [f"{backend.provider}/{backend.model}", f"was={original_key or '-'}"]
        if result.category_key is not None:
            verdict = result.category_key + (" (unknown)" if unknown_key else "")
            parts.append(f"verdict={verdict}")
        if result.menu_item_name is not None:
            parts.append(f"item={result.menu_item_name}")
        parts.append(f"is_item={str(result.is_item).lower()}")
        parts.append(f"conf={result.confidence:.2f}")
        return ", ".join(parts)

    # -- counters -----------------------------------------------------------------------

    def _count_skip(self) -> None:
        with self._lock:
            self._skipped += 1

    def _count_failure(self, message: str) -> None:
        with self._lock:
            self._failures += 1
            self._last_error = message
