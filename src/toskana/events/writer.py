"""Single-writer batched persistence of crossing events and data gaps.

:class:`EventWriter` owns the only thread that writes to the ``events`` and
``data_gaps`` tables. Payloads arrive either through an
:class:`~toskana.events.bus.EventBus` subscription (topics ``crossing`` and
``gap``) or by direct ``enqueue_*`` calls; they are buffered in a queue and
flushed in batches (every ``flush_interval_s`` or ``batch_size`` items,
whichever comes first). ``stop()`` drains the queue and performs a final
flush, so no accepted payload is ever lost on graceful shutdown.
"""

from __future__ import annotations

import logging
import queue
import threading
from collections.abc import Mapping
from typing import Any

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool
from ulid import ULID

from toskana.config import sqlite_url
from toskana.db.base import _set_sqlite_pragmas
from toskana.db.models import DataGap, Event
from toskana.events.bus import TOPIC_CROSSING, TOPIC_GAP, EventBus

logger = logging.getLogger(__name__)

#: ``events`` columns accepted from crossing payloads (extras are ignored).
_EVENT_FIELDS = frozenset(
    {
        "id",
        "restaurant_id",
        "camera_id",
        "line_id",
        "session_id",
        "track_id",
        "category_id",
        "menu_item_id",
        "raw_class_name",
        "confidence",
        "direction",
        "ts",
        "frame_index",
        "anchor_x",
        "anchor_y",
        "snapshot_path",
        "dedup_group_id",
        "is_canonical",
    }
)

_GAP_FIELDS = frozenset({"restaurant_id", "camera_id", "from_ts", "to_ts", "reason"})

_STOP = object()


def new_event_id() -> str:
    """A fresh ULID string (26 chars), the ``events`` primary key format."""
    return str(ULID())


def make_writer_session_factory(db_path: str) -> sessionmaker[Session]:
    """Session factory safe for cross-thread SQLite use.

    ``NullPool`` + ``check_same_thread=False`` means every checkout opens a
    fresh connection, so connections never migrate between the writer
    thread and other threads (WAL allows concurrent readers meanwhile).
    """
    engine = create_engine(
        sqlite_url(db_path),
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    event.listen(engine, "connect", _set_sqlite_pragmas)
    return sessionmaker(bind=engine, expire_on_commit=False)


class EventWriter:
    """Batches ``crossing``/``gap`` payloads into the database from one thread."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        bus: EventBus | None = None,
        flush_interval_s: float = 0.25,
        batch_size: int = 50,
    ) -> None:
        self._session_factory = session_factory
        self._bus = bus
        self._flush_interval_s = flush_interval_s
        self._batch_size = batch_size
        self._queue: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._unsubscribes: list[Any] = []
        self._written_events = 0
        self._written_gaps = 0
        self._counts_lock = threading.Lock()

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("EventWriter already started")
        self._thread = threading.Thread(target=self._run, name="event-writer", daemon=True)
        self._thread.start()
        if self._bus is not None:
            self._unsubscribes.append(self._bus.subscribe(TOPIC_CROSSING, self.enqueue_event))
            self._unsubscribes.append(self._bus.subscribe(TOPIC_GAP, self.enqueue_gap))

    def stop(self) -> None:
        """Graceful stop: unsubscribe, drain the queue, final flush. Idempotent."""
        for unsubscribe in self._unsubscribes:
            unsubscribe()
        self._unsubscribes.clear()
        if self._thread is None:
            return
        self._queue.put(_STOP)
        self._thread.join()
        self._thread = None

    def __enter__(self) -> EventWriter:
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    # -- producers -----------------------------------------------------------

    def enqueue_event(self, payload: Mapping[str, Any]) -> None:
        """Queue one crossing payload (``events`` columns; ``id`` optional)."""
        self._queue.put(("event", dict(payload)))

    def enqueue_gap(self, payload: Mapping[str, Any]) -> None:
        """Queue one data-gap payload (``data_gaps`` columns)."""
        self._queue.put(("gap", dict(payload)))

    # -- observability -------------------------------------------------------

    @property
    def written_events(self) -> int:
        with self._counts_lock:
            return self._written_events

    @property
    def written_gaps(self) -> int:
        with self._counts_lock:
            return self._written_gaps

    # -- writer thread -------------------------------------------------------

    def _run(self) -> None:
        pending: list[tuple[str, dict[str, Any]]] = []
        running = True
        while running:
            try:
                item = self._queue.get(timeout=self._flush_interval_s)
            except queue.Empty:
                self._flush(pending)
                continue
            if item is _STOP:
                running = False
            else:
                pending.append(item)
                if len(pending) >= self._batch_size:
                    self._flush(pending)
        # Drain anything enqueued before the stop sentinel raced in.
        while True:
            try:
                item = self._queue.get_nowait()
            except queue.Empty:
                break
            if item is not _STOP:
                pending.append(item)
        self._flush(pending)

    def _flush(self, pending: list[tuple[str, dict[str, Any]]]) -> None:
        if not pending:
            return
        batch, pending[:] = list(pending), []
        try:
            with self._session_factory() as session:
                n_events = n_gaps = 0
                for kind, payload in batch:
                    if kind == "event":
                        session.add(self._build_event(payload))
                        n_events += 1
                    else:
                        session.add(self._build_gap(payload))
                        n_gaps += 1
                session.commit()
            with self._counts_lock:
                self._written_events += n_events
                self._written_gaps += n_gaps
        except Exception:
            logger.exception("event writer flush failed; dropped %d payload(s)", len(batch))

    @staticmethod
    def _build_event(payload: dict[str, Any]) -> Event:
        fields = {key: value for key, value in payload.items() if key in _EVENT_FIELDS}
        fields.setdefault("id", new_event_id())
        return Event(**fields)

    @staticmethod
    def _build_gap(payload: dict[str, Any]) -> DataGap:
        fields = {key: value for key, value in payload.items() if key in _GAP_FIELDS}
        return DataGap(**fields)
