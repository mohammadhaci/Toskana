"""Pure DedupEngine tests: greedy one-to-one cross-camera matching, window
boundaries, strategies, buffer expiry and bus publishing (hand-fed events)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from toskana.db.base import Base
from toskana.db.models import Camera, ExitGroup, Restaurant
from toskana.events.bus import TOPIC_CORRECTION, TOPIC_CROSSING, TOPIC_DEMOTE, EventBus
from toskana.vision.dedup import DedupEngine, GroupConfig, MatchResult, load_camera_groups

GROUP_ID = 7
CAM_A = 1  # primary in the default config
CAM_B = 2


def make_engine(
    *,
    strategy: str = "primary_wins",
    window_ms: int = 2000,
    primary_camera_id: int | None = CAM_A,
    bus: EventBus | None = None,
) -> DedupEngine:
    config = GroupConfig(
        group_id=GROUP_ID,
        window_ms=window_ms,
        strategy=strategy,
        primary_camera_id=primary_camera_id,
    )
    return DedupEngine({CAM_A: config, CAM_B: config}, bus=bus)


def crossing(
    event_id: str,
    *,
    camera_id: int = CAM_A,
    ts: int = 1_000,
    direction: str = "out",
    category_id: int | None = 10,
    menu_item_id: int | None = None,
    raw_class_name: str = "drink",
    restaurant_id: int = 1,
) -> dict[str, Any]:
    return {
        "id": event_id,
        "restaurant_id": restaurant_id,
        "camera_id": camera_id,
        "ts": ts,
        "direction": direction,
        "category_id": category_id,
        "menu_item_id": menu_item_id,
        "raw_class_name": raw_class_name,
    }


class TestPairMatching:
    def test_pair_match_within_window(self) -> None:
        engine = make_engine()
        assert engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000)) is None
        match = engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_200))
        assert isinstance(match, MatchResult)
        assert match.canonical_event_id == "a1"  # primary_wins: cam A is primary
        assert match.demoted_event_id == "b1"
        assert len(match.dedup_group_id) == 26  # ULID
        stats = engine.stats
        assert (stats.considered, stats.matches, stats.demotions) == (2, 1, 1)

    def test_different_category_never_merged(self) -> None:
        engine = make_engine()
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000, category_id=10))
        other = crossing("b1", camera_id=CAM_B, ts=1_100, category_id=20)
        assert engine.handle_crossing(other) is None
        assert engine.stats.matches == 0

    def test_different_direction_never_merged(self) -> None:
        engine = make_engine()
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000, direction="out"))
        reverse = crossing("b1", camera_id=CAM_B, ts=1_100, direction="in")
        assert engine.handle_crossing(reverse) is None
        assert engine.stats.matches == 0

    def test_same_camera_never_merged(self) -> None:
        engine = make_engine()
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        assert engine.handle_crossing(crossing("a2", camera_id=CAM_A, ts=1_100)) is None
        assert engine.stats.matches == 0

    def test_unknown_camera_is_ignored(self) -> None:
        engine = make_engine()
        assert engine.handle_crossing(crossing("x1", camera_id=99, ts=1_000)) is None
        assert engine.stats.considered == 0

    def test_menu_item_identity_when_present(self) -> None:
        engine = make_engine()
        # Same category but different menu items: not the same physical item.
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000, menu_item_id=5))
        assert (
            engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_100, menu_item_id=6))
            is None
        )
        # Same menu item: merged.
        match = engine.handle_crossing(crossing("b2", camera_id=CAM_B, ts=1_200, menu_item_id=5))
        assert match is not None and match.demoted_event_id == "b2"

    def test_unmapped_events_match_on_raw_class_name(self) -> None:
        engine = make_engine()
        engine.handle_crossing(
            crossing("a1", camera_id=CAM_A, ts=1_000, category_id=None, raw_class_name="cup")
        )
        assert (
            engine.handle_crossing(
                crossing("b1", camera_id=CAM_B, ts=1_100, category_id=None, raw_class_name="bowl")
            )
            is None
        )
        match = engine.handle_crossing(
            crossing("b2", camera_id=CAM_B, ts=1_200, category_id=None, raw_class_name="cup")
        )
        assert match is not None


class TestWindowBoundary:
    def test_delta_exactly_window_matches(self) -> None:
        engine = make_engine(window_ms=2_000)
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        match = engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=3_000))
        assert match is not None

    def test_delta_window_plus_one_does_not_match(self) -> None:
        engine = make_engine(window_ms=2_000)
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        assert engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=3_001)) is None
        assert engine.stats.matches == 0


class TestStrategies:
    def test_primary_wins_demotes_non_primary_even_when_primary_is_later(self) -> None:
        engine = make_engine(strategy="primary_wins", primary_camera_id=CAM_A)
        engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_000))  # earlier, non-primary
        match = engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_200))
        assert match is not None
        assert match.canonical_event_id == "a1"
        assert match.demoted_event_id == "b1"

    def test_primary_wins_without_primary_falls_back_to_first_wins(self) -> None:
        engine = make_engine(strategy="primary_wins", primary_camera_id=None)
        engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_000))
        match = engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_200))
        assert match is not None
        assert match.canonical_event_id == "b1"  # earliest wins
        assert match.demoted_event_id == "a1"

    def test_first_wins_demotes_the_later_event(self) -> None:
        engine = make_engine(strategy="first_wins", primary_camera_id=CAM_A)
        engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_000))
        match = engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_200))
        assert match is not None
        assert match.canonical_event_id == "b1"  # earlier ts wins despite primary flag
        assert match.demoted_event_id == "a1"


class TestGreedyOneToOne:
    def test_two_a_events_one_b_event_pair_only_once(self) -> None:
        """2 events on cam A + 1 on cam B -> one pair; canonical count 2 of 3."""
        engine = make_engine()
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        engine.handle_crossing(crossing("a2", camera_id=CAM_A, ts=1_100))
        match = engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_150))
        assert match is not None
        assert match.canonical_event_id == "a1"  # earliest-first greedy pick
        assert match.demoted_event_id == "b1"
        assert engine.stats.matches == 1  # a2 stays unpaired (and canonical)

    def test_consumed_counterpart_never_reused(self) -> None:
        engine = make_engine()
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        assert engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=1_100)) is not None
        # A second cam-B sighting must not re-pair with the consumed a1.
        assert engine.handle_crossing(crossing("b2", camera_id=CAM_B, ts=1_200)) is None
        assert engine.stats.matches == 1

    def test_three_items_under_two_cameras_form_three_pairs(self) -> None:
        """Multiplicity: a 3-item tray under 2 cameras -> exactly 3 dedup groups."""
        engine = make_engine()
        items = [("drink", 10), ("main", 20), ("main", 20)]
        for i, (raw, cat) in enumerate(items):
            assert (
                engine.handle_crossing(
                    crossing(
                        f"a{i}", camera_id=CAM_A, ts=1_000, category_id=cat, raw_class_name=raw
                    )
                )
                is None
            )
        matches = []
        for i, (raw, cat) in enumerate(items):
            match = engine.handle_crossing(
                crossing(f"b{i}", camera_id=CAM_B, ts=1_200, category_id=cat, raw_class_name=raw)
            )
            assert match is not None
            matches.append(match)
        assert engine.stats.matches == 3
        assert len({m.dedup_group_id for m in matches}) == 3  # three distinct groups
        assert {m.canonical_event_id for m in matches} == {"a0", "a1", "a2"}
        assert {m.demoted_event_id for m in matches} == {"b0", "b1", "b2"}


class TestBufferExpiry:
    def test_stale_entry_expires_and_cannot_match(self) -> None:
        engine = make_engine(window_ms=2_000)
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        assert engine.buffered(GROUP_ID) == 1
        # 3 s later: outside the window -> no match, and a1 is expired.
        assert engine.handle_crossing(crossing("b1", camera_id=CAM_B, ts=4_000)) is None
        stats = engine.stats
        assert stats.matches == 0
        assert stats.expired == 1
        assert engine.buffered(GROUP_ID) == 1  # only b1 remains

    def test_explicit_expire_flushes_old_entries(self) -> None:
        engine = make_engine(window_ms=2_000)
        engine.handle_crossing(crossing("a1", camera_id=CAM_A, ts=1_000))
        assert engine.expire(10_000) == 1
        assert engine.buffered(GROUP_ID) == 0
        assert engine.stats.expired == 1


class TestBusIntegration:
    def test_match_publishes_demotes_and_correction(self) -> None:
        bus = EventBus()
        demotes: list[dict[str, Any]] = []
        corrections: list[dict[str, Any]] = []
        bus.subscribe(TOPIC_DEMOTE, demotes.append)
        bus.subscribe(TOPIC_CORRECTION, corrections.append)

        engine = make_engine(bus=bus)
        engine.start()
        try:
            bus.publish(TOPIC_CROSSING, crossing("a1", camera_id=CAM_A, ts=1_000))
            bus.publish(TOPIC_CROSSING, crossing("b1", camera_id=CAM_B, ts=1_200))
        finally:
            engine.stop()

        assert [d["event_id"] for d in demotes] == ["a1", "b1"]
        canonical_update, demote_update = demotes
        assert canonical_update["is_canonical"] is True
        assert demote_update["is_canonical"] is False
        gid = demote_update["dedup_group_id"]
        assert canonical_update["dedup_group_id"] == gid and len(gid) == 26

        (correction,) = corrections
        assert correction["event_id"] == "b1"
        assert correction["dedup_group_id"] == gid
        assert correction["is_canonical"] is False
        assert correction["category_id"] == 10
        assert correction["direction"] == "out"
        assert correction["camera_id"] == CAM_B

        # After stop() the engine no longer consumes crossings.
        bus.publish(TOPIC_CROSSING, crossing("a2", camera_id=CAM_A, ts=1_300))
        assert engine.stats.considered == 2


class TestLoadCameraGroups:
    def test_groups_with_fewer_than_two_cameras_are_skipped(self) -> None:
        engine = create_engine("sqlite+pysqlite:///:memory:")
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(Restaurant(id=1, slug="r", name="R"))
            session.flush()
            session.add_all(
                [
                    ExitGroup(
                        id=1,
                        restaurant_id=1,
                        name="pass",
                        dedup_window_ms=1500,
                        dedup_strategy="first_wins",
                    ),
                    ExitGroup(id=2, restaurant_id=1, name="solo"),
                ]
            )
            session.flush()
            session.add_all(
                [
                    Camera(
                        id=1,
                        restaurant_id=1,
                        name="a",
                        source_type="file",
                        source_url="a",
                        exit_group_id=1,
                        is_primary_in_group=True,
                    ),
                    Camera(
                        id=2,
                        restaurant_id=1,
                        name="b",
                        source_type="file",
                        source_url="b",
                        exit_group_id=1,
                    ),
                    Camera(
                        id=3,
                        restaurant_id=1,
                        name="c",
                        source_type="file",
                        source_url="c",
                        exit_group_id=2,
                    ),
                    Camera(
                        id=4,
                        restaurant_id=1,
                        name="d",
                        source_type="file",
                        source_url="d",
                        exit_group_id=1,
                        enabled=False,
                    ),
                    Camera(id=5, restaurant_id=1, name="e", source_type="file", source_url="e"),
                ]
            )
            session.commit()
            camera_groups = load_camera_groups(session, 1)

        assert set(camera_groups) == {1, 2}  # only the 2-camera group's members
        config = camera_groups[1]
        assert camera_groups[2] is config
        assert config.group_id == 1
        assert config.window_ms == 1500
        assert config.strategy == "first_wins"
        assert config.primary_camera_id == 1
