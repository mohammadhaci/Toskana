"""Time-helper tests, including the graceful UTC fallback.

The dashboard must never crash-loop because a machine lacks a timezone
database (Windows without ``tzdata``); an unknown/unavailable zone degrades
to UTC instead.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from toskana.api.timeutils import (
    _zone,
    bucket_start,
    local_day_bounds,
    local_today,
)


def test_known_zone_is_dst_aware() -> None:
    # 2024-03-31 is the spring-forward day in Europe/Vienna: a 23-hour day.
    start, end = local_day_bounds("Europe/Vienna", date(2024, 3, 31))
    assert (end - start) == 23 * 3600 * 1000


def test_unavailable_zone_falls_back_to_utc() -> None:
    assert _zone("Definitely/NotAZone") is UTC


def test_fallback_day_bounds_are_utc_midnights() -> None:
    start, end = local_day_bounds("Definitely/NotAZone", date(2024, 6, 1))
    assert start == int(datetime(2024, 6, 1, tzinfo=UTC).timestamp() * 1000)
    assert (end - start) == 24 * 3600 * 1000


def test_fallback_helpers_do_not_raise() -> None:
    assert isinstance(local_today("Definitely/NotAZone", now_ms=0), date)
    assert bucket_start(0, "Definitely/NotAZone", "hour").tzinfo is UTC
