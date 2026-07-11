"""Crossing-event log: filtered list, single event, CSV export, snapshot,
manual canonical toggle (dedup review)."""

from __future__ import annotations

import csv
import io
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import Select, func, select
from sqlalchemy.orm import selectinload

from toskana.api import schemas
from toskana.api.deps import ConfigDep, PageDep, SessionDep, restaurant_or_404
from toskana.db.models import Camera, Category, DataGap, Event, MenuItem

router = APIRouter(tags=["events"])

_EXPORT_CHUNK = 500

CSV_COLUMNS = [
    "id",
    "ts",
    "ts_iso_utc",
    "camera_id",
    "camera_name",
    "line_id",
    "direction",
    "category_id",
    "category_key",
    "menu_item_id",
    "menu_item_name",
    "raw_class_name",
    "confidence",
    "is_canonical",
    "dedup_group_id",
    "snapshot_path",
]


def _filtered(
    restaurant_id: int,
    *,
    from_ts: int | None,
    to_ts: int | None,
    camera_id: int | None,
    category_id: int | None,
    menu_item_id: int | None,
    direction: str | None,
    canonical_only: bool,
    q: str | None,
) -> Select[tuple[Event]]:
    stmt = select(Event).where(Event.restaurant_id == restaurant_id)
    if from_ts is not None:
        stmt = stmt.where(Event.ts >= from_ts)
    if to_ts is not None:
        stmt = stmt.where(Event.ts < to_ts)
    if camera_id is not None:
        stmt = stmt.where(Event.camera_id == camera_id)
    if category_id is not None:
        stmt = stmt.where(Event.category_id == category_id)
    if menu_item_id is not None:
        stmt = stmt.where(Event.menu_item_id == menu_item_id)
    if direction is not None:
        stmt = stmt.where(Event.direction == direction)
    if canonical_only:
        stmt = stmt.where(Event.is_canonical.is_(True))
    if q:
        stmt = stmt.where(Event.raw_class_name.like(f"%{q}%"))
    return stmt


@router.get("/restaurants/{restaurant_id}/events", response_model=schemas.Page[schemas.EventRead])
def list_events(
    restaurant_id: int,
    session: SessionDep,
    page: PageDep,
    from_ts: int | None = None,
    to_ts: int | None = None,
    camera_id: int | None = None,
    category_id: int | None = None,
    menu_item_id: int | None = None,
    direction: Literal["out", "in"] | None = None,
    canonical_only: bool = True,
    q: str | None = None,
) -> dict:
    restaurant_or_404(session, restaurant_id)
    stmt = _filtered(
        restaurant_id,
        from_ts=from_ts,
        to_ts=to_ts,
        camera_id=camera_id,
        category_id=category_id,
        menu_item_id=menu_item_id,
        direction=direction,
        canonical_only=canonical_only,
        q=q,
    )
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.options(selectinload(Event.menu_item))  # menu_item_name without N+1
        .order_by(Event.ts.desc(), Event.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/restaurants/{restaurant_id}/events/export.csv")
def export_events_csv(
    restaurant_id: int,
    session: SessionDep,
    from_ts: int | None = None,
    to_ts: int | None = None,
    camera_id: int | None = None,
    category_id: int | None = None,
    menu_item_id: int | None = None,
    direction: Literal["out", "in"] | None = None,
    canonical_only: bool = True,
    q: str | None = None,
) -> StreamingResponse:
    restaurant_or_404(session, restaurant_id)
    stmt = _filtered(
        restaurant_id,
        from_ts=from_ts,
        to_ts=to_ts,
        camera_id=camera_id,
        category_id=category_id,
        menu_item_id=menu_item_id,
        direction=direction,
        canonical_only=canonical_only,
        q=q,
    ).order_by(Event.ts.asc(), Event.id.asc())

    camera_names: dict[int, str] = {
        row.id: row.name
        for row in session.execute(
            select(Camera.id, Camera.name).where(Camera.restaurant_id == restaurant_id)
        )
    }
    category_keys: dict[int | None, str] = {
        row.id: row.key
        for row in session.execute(
            select(Category.id, Category.key).where(Category.restaurant_id == restaurant_id)
        )
    }
    menu_item_names: dict[int | None, str] = {
        row.id: row.name
        for row in session.execute(
            select(MenuItem.id, MenuItem.name).where(MenuItem.restaurant_id == restaurant_id)
        )
    }

    def rows() -> Iterator[str]:
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(CSV_COLUMNS)
        yield buffer.getvalue()
        offset = 0
        while True:
            batch = session.scalars(stmt.limit(_EXPORT_CHUNK).offset(offset)).all()
            if not batch:
                return
            offset += len(batch)
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            for event in batch:
                ts_iso = datetime.fromtimestamp(event.ts / 1000.0, tz=UTC).isoformat()
                writer.writerow(
                    [
                        event.id,
                        event.ts,
                        ts_iso,
                        event.camera_id,
                        camera_names.get(event.camera_id, ""),
                        event.line_id if event.line_id is not None else "",
                        event.direction,
                        event.category_id if event.category_id is not None else "",
                        category_keys.get(event.category_id, ""),
                        event.menu_item_id if event.menu_item_id is not None else "",
                        menu_item_names.get(event.menu_item_id, ""),
                        event.raw_class_name,
                        f"{event.confidence:.4f}",
                        int(event.is_canonical),
                        event.dedup_group_id or "",
                        event.snapshot_path or "",
                    ]
                )
            yield buffer.getvalue()

    return StreamingResponse(
        rows(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="events.csv"'},
    )


@router.get(
    "/restaurants/{restaurant_id}/data-gaps", response_model=schemas.Page[schemas.DataGapRead]
)
def list_data_gaps(
    restaurant_id: int,
    session: SessionDep,
    page: PageDep,
    from_ts: int | None = None,
    to_ts: int | None = None,
    camera_id: int | None = None,
) -> dict:
    """Recorded no-data intervals (outages, start/stop and drift markers).

    A gap overlaps the ``[from_ts, to_ts)`` window when it starts before the
    window end and has not ended before the window start (open gaps count).
    """
    restaurant_or_404(session, restaurant_id)
    stmt = select(DataGap).where(DataGap.restaurant_id == restaurant_id)
    if from_ts is not None:
        stmt = stmt.where((DataGap.to_ts.is_(None)) | (DataGap.to_ts >= from_ts))
    if to_ts is not None:
        stmt = stmt.where(DataGap.from_ts < to_ts)
    if camera_id is not None:
        stmt = stmt.where(DataGap.camera_id == camera_id)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.scalars(
        stmt.order_by(DataGap.from_ts.desc(), DataGap.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


def _event_or_404(session: SessionDep, restaurant_id: int, event_id: str) -> Event:
    """Tenant-scoped event lookup: a foreign-tenant event is a 404."""
    restaurant_or_404(session, restaurant_id)
    event = session.get(Event, event_id)
    if event is None or event.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="event not found")
    return event


@router.get("/restaurants/{restaurant_id}/events/{event_id}", response_model=schemas.EventRead)
def get_event(restaurant_id: int, event_id: str, session: SessionDep) -> Event:
    return _event_or_404(session, restaurant_id, event_id)


@router.patch("/restaurants/{restaurant_id}/events/{event_id}", response_model=schemas.EventRead)
def patch_event(
    restaurant_id: int, event_id: str, body: schemas.EventPatch, session: SessionDep
) -> Event:
    """Manual dedup review: promote/demote the canonical flag of an event.

    (M8 will additionally push a WS correction message so live counters
    adjust; the flag itself is already the source of truth for stats.)
    """
    event = _event_or_404(session, restaurant_id, event_id)
    event.is_canonical = body.is_canonical
    session.commit()
    return event


@router.get("/restaurants/{restaurant_id}/events/{event_id}/snapshot")
def get_event_snapshot(
    restaurant_id: int, event_id: str, session: SessionDep, config: ConfigDep
) -> FileResponse:
    event = _event_or_404(session, restaurant_id, event_id)
    if not event.snapshot_path:
        raise HTTPException(status_code=404, detail="event has no snapshot")
    base = Path(config.snapshots_dir).resolve()
    path = (base / event.snapshot_path).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(status_code=404, detail="snapshot file not found")
    return FileResponse(path, media_type="image/jpeg")
