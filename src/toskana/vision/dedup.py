"""Cross-camera duplicate suppression within exit groups (M8).

Every raw crossing is always persisted (the pipeline emits it on the bus and
the :class:`~toskana.events.writer.EventWriter` inserts it optimistically
canonical). The :class:`DedupEngine` subscribes to the same ``crossing``
topic *after* the writer, keeps a sliding buffer of recent events per exit
group and, when two events from **different cameras** of the same group
carry the **same direction** and the **same identity** (same menu item when
both resolved one, else same category, else same raw class name) within the
group's ``dedup_window_ms``, greedily pairs them one-to-one (earliest
buffered first — 3 items under 2 cameras become 3 pairs, never a mega-merge).

On a match both events receive a shared ``dedup_group_id`` (ULID) and one of
them is demoted per the group's strategy:

* ``primary_wins`` (default) — the event from the non-primary camera is
  demoted (fallback to ``first_wins`` when neither/both cameras are primary),
* ``first_wins`` — the event with the later timestamp is demoted.

Single-writer discipline is preserved: the engine never touches the DB. It
publishes ``demote`` payloads that the EventWriter applies as ``UPDATE``s on
its own thread (the insert is always queued before the demotion because the
writer subscribes to ``crossing`` first), plus one ``dedup_correction``
payload per demotion that ``/ws/live`` forwards as a ``correction`` message
so live counters can decrement.

Documented limitation: two sightings of the same physical item further apart
than the window (e.g. 3 s) are *not* merged — both stay canonical. The
window is per exit group (``exit_groups.dedup_window_ms``) and should cover
the worst inter-camera latency; camera clock drift directly eats into it.
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from toskana.db.models import Camera, ExitGroup, Restaurant
from toskana.events.bus import TOPIC_CORRECTION, TOPIC_CROSSING, TOPIC_DEMOTE, EventBus
from toskana.events.writer import new_event_id

logger = logging.getLogger(__name__)

STRATEGY_PRIMARY_WINS = "primary_wins"
STRATEGY_FIRST_WINS = "first_wins"


@dataclass(frozen=True)
class GroupConfig:
    """Dedup settings of one exit group, attached to each member camera."""

    group_id: int
    window_ms: int = 2000
    strategy: str = STRATEGY_PRIMARY_WINS
    primary_camera_id: int | None = None

    def __post_init__(self) -> None:
        if self.strategy not in (STRATEGY_PRIMARY_WINS, STRATEGY_FIRST_WINS):
            raise ValueError(f"unknown dedup strategy: {self.strategy!r}")
        if self.window_ms < 0:
            raise ValueError("window_ms must be >= 0")


@dataclass
class DedupStats:
    """Observability counters (thread-safe snapshots via :meth:`DedupEngine.stats`)."""

    considered: int = 0  # crossings from cameras that belong to a group
    matches: int = 0  # pairs formed
    demotions: int = 0  # events demoted (== matches with 1:1 pairing)
    expired: int = 0  # unmatched buffer entries dropped past the window


@dataclass
class _BufferedEvent:
    event_id: str
    camera_id: int
    ts: int
    direction: str
    identity: tuple[str, Any]
    restaurant_id: int | None
    category_id: int | None
    menu_item_id: int | None
    consumed: bool = False


@dataclass(frozen=True)
class MatchResult:
    """Outcome of one greedy pair match (also what gets published)."""

    group_id: int
    dedup_group_id: str
    canonical_event_id: str
    demoted_event_id: str


def _identity(payload: Mapping[str, Any]) -> tuple[str, Any]:
    """What must be equal for two events to be the same physical item.

    Menu item when resolved (Phase 2), else category (Phase 1), else the raw
    model class name (unmapped classes still never cross-merge arbitrarily).
    """
    menu_item_id = payload.get("menu_item_id")
    if menu_item_id is not None:
        return ("menu", menu_item_id)
    category_id = payload.get("category_id")
    if category_id is not None:
        return ("cat", category_id)
    return ("raw", payload.get("raw_class_name"))


class DedupEngine:
    """Greedy one-to-one cross-camera dedup for the exit groups of one site."""

    def __init__(
        self,
        camera_groups: Mapping[int, GroupConfig],
        *,
        bus: EventBus | None = None,
    ) -> None:
        self._camera_groups = dict(camera_groups)
        self._bus = bus
        self._lock = threading.Lock()
        self._buffers: dict[int, list[_BufferedEvent]] = defaultdict(list)
        self._latest_ts: dict[int, int] = {}
        self._stats = DedupStats()
        self._unsubscribe: Any = None

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        """Subscribe to the ``crossing`` topic (after the EventWriter, so the
        optimistic insert is always queued before any demotion update)."""
        if self._bus is None:
            raise RuntimeError("DedupEngine has no bus to subscribe to")
        if self._unsubscribe is not None:
            raise RuntimeError("DedupEngine already started")
        self._unsubscribe = self._bus.subscribe(TOPIC_CROSSING, self._on_crossing)

    def stop(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
            self._unsubscribe = None

    # -- observability ---------------------------------------------------------

    @property
    def stats(self) -> DedupStats:
        with self._lock:
            return DedupStats(
                considered=self._stats.considered,
                matches=self._stats.matches,
                demotions=self._stats.demotions,
                expired=self._stats.expired,
            )

    @property
    def camera_groups(self) -> dict[int, GroupConfig]:
        return dict(self._camera_groups)

    def buffered(self, group_id: int) -> int:
        """Unconsumed entries currently buffered for a group (tests)."""
        with self._lock:
            return sum(1 for entry in self._buffers.get(group_id, ()) if not entry.consumed)

    # -- core ------------------------------------------------------------------

    def _on_crossing(self, payload: Any) -> None:
        """Bus subscriber adapter (the bus expects a None-returning callable)."""
        self.handle_crossing(payload)

    def handle_crossing(self, payload: Mapping[str, Any]) -> MatchResult | None:
        """Process one crossing payload; returns the match, if one was made.

        Thread-safe (called from every pipeline thread via the bus). Events
        from cameras outside any configured exit group are ignored.
        """
        camera_id = payload.get("camera_id")
        config = self._camera_groups.get(camera_id) if camera_id is not None else None
        if config is None:
            return None
        entry = _BufferedEvent(
            event_id=payload["id"],
            camera_id=payload["camera_id"],
            ts=payload["ts"],
            direction=payload["direction"],
            identity=_identity(payload),
            restaurant_id=payload.get("restaurant_id"),
            category_id=payload.get("category_id"),
            menu_item_id=payload.get("menu_item_id"),
        )
        with self._lock:
            self._stats.considered += 1
            self._expire_locked(config, entry.ts)
            match, partner = self._match_locked(config, entry)
        if match is not None and partner is not None:
            self._publish_match(match, entry, partner)
        return match

    def expire(self, now_ts: int) -> int:
        """Drop unmatched entries older than each group's window; returns count."""
        dropped = 0
        with self._lock:
            before = self._stats.expired
            for config in {c.group_id: c for c in self._camera_groups.values()}.values():
                self._expire_locked(config, now_ts)
            dropped = self._stats.expired - before
        return dropped

    # -- internals (call with the lock held) ------------------------------------

    def _expire_locked(self, config: GroupConfig, ts: int) -> None:
        latest = max(self._latest_ts.get(config.group_id, ts), ts)
        self._latest_ts[config.group_id] = latest
        cutoff = latest - config.window_ms
        buffer = self._buffers[config.group_id]
        kept: list[_BufferedEvent] = []
        for entry in buffer:
            if entry.ts >= cutoff:
                kept.append(entry)
            elif not entry.consumed:
                self._stats.expired += 1
        buffer[:] = kept

    def _match_locked(
        self, config: GroupConfig, entry: _BufferedEvent
    ) -> tuple[MatchResult | None, _BufferedEvent | None]:
        buffer = self._buffers[config.group_id]
        partner: _BufferedEvent | None = None
        for candidate in sorted(buffer, key=lambda e: e.ts):  # greedy, earliest-first
            if (
                not candidate.consumed
                and candidate.camera_id != entry.camera_id
                and candidate.direction == entry.direction
                and candidate.identity == entry.identity
                and abs(entry.ts - candidate.ts) <= config.window_ms
            ):
                partner = candidate
                break
        if partner is None:
            buffer.append(entry)
            return None, None

        partner.consumed = True
        entry.consumed = True
        buffer.append(entry)  # kept (consumed) so it can never pair again
        canonical, demoted = self._pick_canonical(config, partner, entry)
        match = MatchResult(
            group_id=config.group_id,
            dedup_group_id=new_event_id(),
            canonical_event_id=canonical.event_id,
            demoted_event_id=demoted.event_id,
        )
        self._stats.matches += 1
        self._stats.demotions += 1
        return match, partner

    @staticmethod
    def _pick_canonical(
        config: GroupConfig, first: _BufferedEvent, second: _BufferedEvent
    ) -> tuple[_BufferedEvent, _BufferedEvent]:
        """Return ``(canonical, demoted)`` per the group's strategy."""
        if config.strategy == STRATEGY_PRIMARY_WINS:
            if first.camera_id == config.primary_camera_id:
                return first, second
            if second.camera_id == config.primary_camera_id:
                return second, first
            # No primary among the pair: fall back to first_wins.
        if second.ts < first.ts:
            return second, first
        return first, second  # tie: the earlier-buffered event wins

    # -- publishing (no lock held) ------------------------------------------------

    def _publish_match(
        self, match: MatchResult, entry: _BufferedEvent, partner: _BufferedEvent
    ) -> None:
        if self._bus is None:
            return
        demoted = entry if entry.event_id == match.demoted_event_id else partner
        # The EventWriter applies these as UPDATEs (single-writer discipline).
        self._bus.publish(
            TOPIC_DEMOTE,
            {
                "event_id": match.canonical_event_id,
                "dedup_group_id": match.dedup_group_id,
                "is_canonical": True,
            },
        )
        self._bus.publish(
            TOPIC_DEMOTE,
            {
                "event_id": match.demoted_event_id,
                "dedup_group_id": match.dedup_group_id,
                "is_canonical": False,
            },
        )
        # Live counters decrement on this (forwarded by /ws/live as
        # ``{type: "correction", ...}``).
        self._bus.publish(
            TOPIC_CORRECTION,
            {
                "event_id": match.demoted_event_id,
                "dedup_group_id": match.dedup_group_id,
                "is_canonical": False,
                "restaurant_id": demoted.restaurant_id,
                "camera_id": demoted.camera_id,
                "category_id": demoted.category_id,
                "menu_item_id": demoted.menu_item_id,
                "direction": demoted.direction,
                "ts": demoted.ts,
            },
        )


# -- configuration from the DB ---------------------------------------------------


def load_camera_groups(session: Session, restaurant_id: int) -> dict[int, GroupConfig]:
    """``camera_id -> GroupConfig`` for every exit group with >= 2 enabled cameras."""
    rows = session.execute(
        select(
            Camera.id,
            Camera.is_primary_in_group,
            ExitGroup.id,
            ExitGroup.dedup_window_ms,
            ExitGroup.dedup_strategy,
        )
        .join(ExitGroup, Camera.exit_group_id == ExitGroup.id)
        .where(Camera.restaurant_id == restaurant_id, Camera.enabled.is_(True))
        .order_by(ExitGroup.id, Camera.id)
    ).all()
    by_group: dict[int, list[tuple[int, bool, int, str]]] = defaultdict(list)
    for camera_id, is_primary, group_id, window_ms, strategy in rows:
        by_group[group_id].append((camera_id, bool(is_primary), window_ms, strategy))
    camera_groups: dict[int, GroupConfig] = {}
    for group_id, members in by_group.items():
        if len(members) < 2:
            continue  # a single camera cannot double-count
        primary_camera_id = next(
            (camera_id for camera_id, is_primary, _, _ in members if is_primary), None
        )
        _, _, window_ms, strategy = members[0]
        config = GroupConfig(
            group_id=group_id,
            window_ms=window_ms,
            strategy=strategy,
            primary_camera_id=primary_camera_id,
        )
        for camera_id, _, _, _ in members:
            camera_groups[camera_id] = config
    return camera_groups


def build_dedup_engine(
    session_factory: Any, restaurant_slug: str, *, bus: EventBus
) -> DedupEngine | None:
    """Engine for the active restaurant, or None when no group needs dedup."""
    with session_factory() as session:
        restaurant = session.scalar(select(Restaurant).where(Restaurant.slug == restaurant_slug))
        if restaurant is None:
            return None
        camera_groups = load_camera_groups(session, restaurant.id)
    if not camera_groups:
        return None
    logger.info(
        "dedup engine active for %d camera(s) in %d exit group(s)",
        len(camera_groups),
        len({c.group_id for c in camera_groups.values()}),
    )
    return DedupEngine(camera_groups, bus=bus)
