"""Aggregated statistics over canonical events, bucketed in the
restaurant's local timezone (DST-correct: 23h/25h days, no phantom hours)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from sqlalchemy import select

from toskana.api import schemas
from toskana.api.deps import SessionDep, restaurant_or_404
from toskana.api.timeutils import bucket_start, epoch_ms, local_day_bounds, local_today
from toskana.db.models import Category, Event

router = APIRouter(prefix="/restaurants/{restaurant_id}/stats", tags=["stats"])

GroupBy = Literal["category", "camera", "direction"]


@router.get("/timeseries", response_model=schemas.TimeseriesResponse)
def timeseries(
    restaurant_id: int,
    session: SessionDep,
    bucket: Literal["hour", "day"] = "hour",
    from_ts: int | None = None,
    to_ts: int | None = None,
    group_by: GroupBy | None = None,
) -> schemas.TimeseriesResponse:
    restaurant = restaurant_or_404(session, restaurant_id)
    tz_name = restaurant.timezone

    stmt = select(Event.ts, Event.direction, Event.category_id, Event.camera_id).where(
        Event.restaurant_id == restaurant_id, Event.is_canonical.is_(True)
    )
    if from_ts is not None:
        stmt = stmt.where(Event.ts >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(Event.ts < to_ts)

    # (bucket_start_datetime, group_key) -> [out, in]
    counters: dict[tuple[int, str, str | None], list[int]] = {}
    for ts, direction, category_id, camera_id in session.execute(stmt):
        start = bucket_start(ts, tz_name, bucket)
        group: str | None
        if group_by == "category":
            group = str(category_id) if category_id is not None else None
        elif group_by == "camera":
            group = str(camera_id)
        elif group_by == "direction":
            group = direction
        else:
            group = None
        key = (epoch_ms(start), start.isoformat(), group)
        pair = counters.setdefault(key, [0, 0])
        pair[0 if direction == "out" else 1] += 1

    rows = [
        schemas.TimeseriesRow(
            bucket_ts=bucket_ts,
            bucket_iso=bucket_iso,
            group=group,
            out=out,
            in_=in_,
            net=out - in_,
        )
        for (bucket_ts, bucket_iso, group), (out, in_) in sorted(
            counters.items(), key=lambda item: (item[0][0], item[0][2] or "")
        )
    ]
    return schemas.TimeseriesResponse(
        bucket=bucket,
        group_by=group_by,
        timezone=tz_name,
        from_ts=from_ts,
        to_ts=to_ts,
        rows=rows,
    )


@router.get("/summary", response_model=schemas.StatsSummary)
def summary(restaurant_id: int, session: SessionDep) -> schemas.StatsSummary:
    """Today's totals per category (out / in / net), restaurant-local day."""
    restaurant = restaurant_or_404(session, restaurant_id)
    tz_name = restaurant.timezone
    today = local_today(tz_name)
    day_from, day_to = local_day_bounds(tz_name, today)

    per_category: dict[int | None, list[int]] = {}
    stmt = select(Event.category_id, Event.direction).where(
        Event.restaurant_id == restaurant_id,
        Event.is_canonical.is_(True),
        Event.ts >= day_from,
        Event.ts < day_to,
    )
    for category_id, direction in session.execute(stmt):
        pair = per_category.setdefault(category_id, [0, 0])
        pair[0 if direction == "out" else 1] += 1

    categories = session.scalars(
        select(Category)
        .where(Category.restaurant_id == restaurant_id)
        .order_by(Category.sort_order, Category.id)
    ).all()

    rows: list[schemas.CategoryCounter] = []
    for category in categories:
        out, in_ = per_category.get(category.id, [0, 0])
        rows.append(
            schemas.CategoryCounter(
                category_id=category.id,
                key=category.key,
                name_de=category.name_de,
                name_en=category.name_en,
                color_hex=category.color_hex,
                out=out,
                in_=in_,
                net=out - in_,
            )
        )
    if None in per_category:  # events whose class had no mapping
        out, in_ = per_category[None]
        rows.append(
            schemas.CategoryCounter(
                category_id=None,
                key=None,
                name_de=None,
                name_en=None,
                color_hex=None,
                out=out,
                in_=in_,
                net=out - in_,
            )
        )
    total_out = sum(row.out for row in rows)
    total_in = sum(row.in_ for row in rows)
    return schemas.StatsSummary(
        date=today.isoformat(),
        timezone=tz_name,
        from_ts=day_from,
        to_ts=day_to,
        categories=rows,
        total_out=total_out,
        total_in=total_in,
        total_net=total_out - total_in,
    )
