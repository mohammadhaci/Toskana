"""Timezone-aware time helpers for stats/reconcile (restaurant-local days)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def local_day_bounds(tz_name: str, day: date) -> tuple[int, int]:
    """[start, end) of a local calendar day as UTC epoch ms.

    DST-safe: bounds are built from local midnights, so transition days are
    23 or 25 hours long as appropriate.
    """
    tz = ZoneInfo(tz_name)
    start = datetime.combine(day, time(), tzinfo=tz)
    end = datetime.combine(day + timedelta(days=1), time(), tzinfo=tz)
    return epoch_ms(start), epoch_ms(end)


def local_today(tz_name: str, *, now_ms: int | None = None) -> date:
    tz = ZoneInfo(tz_name)
    if now_ms is None:
        return datetime.now(tz).date()
    return datetime.fromtimestamp(now_ms / 1000.0, tz=tz).date()


def bucket_start(ts_ms: int, tz_name: str, bucket: str) -> datetime:
    """Local bucket start (hour/day) containing the UTC epoch-ms instant.

    Working from the instant (``fromtimestamp`` on an aware tz) keeps DST
    correct: the skipped hour never appears and the repeated hour keeps its
    fold, so the two occurrences map to distinct epoch timestamps.
    """
    tz = ZoneInfo(tz_name)
    dt = datetime.fromtimestamp(ts_ms / 1000.0, tz=tz)
    if bucket == "day":
        return dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if bucket == "hour":
        return dt.replace(minute=0, second=0, microsecond=0)
    raise ValueError(f"unknown bucket: {bucket!r}")
