"""POS reconciliation: upload a cashier CSV, compare against counted events.

CSV columns: ``category_key,quantity[,date]`` (date ``YYYY-MM-DD``, local to
the restaurant; rows without a date use the ``date`` query param, defaulting
to today). Every report is persisted as a ``reconcile_runs`` row (ULID id,
uploaded filename, row payload, totals) and listed via
``GET /restaurants/{id}/reconcile-runs`` for audit history.
"""

from __future__ import annotations

import csv
import io
import json
import time
from datetime import date as date_type
from datetime import datetime

from fastapi import APIRouter, HTTPException, UploadFile
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, restaurant_or_404
from toskana.api.timeutils import local_day_bounds, local_today
from toskana.db.models import Category, Event, ReconcileRun
from toskana.events.writer import new_event_id

router = APIRouter(tags=["reconcile"])

REQUIRED_COLUMNS = {"category_key", "quantity"}


def _parse_date(value: str) -> date_type:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"invalid date: {value!r}") from exc


@router.post("/restaurants/{restaurant_id}/reconcile", response_model=schemas.ReconcileReport)
def reconcile(
    restaurant_id: int,
    file: UploadFile,
    session: SessionDep,
    date: str | None = None,
) -> schemas.ReconcileReport:
    restaurant = restaurant_or_404(session, restaurant_id)
    tz_name = restaurant.timezone
    default_day = _parse_date(date) if date is not None else local_today(tz_name)

    try:
        text = file.file.read().decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=422, detail="CSV must be UTF-8 encoded") from exc
    reader = csv.DictReader(io.StringIO(text))
    fieldnames = {name.strip().lower() for name in (reader.fieldnames or [])}
    if not REQUIRED_COLUMNS.issubset(fieldnames):
        missing = sorted(REQUIRED_COLUMNS - fieldnames)
        raise HTTPException(status_code=422, detail=f"CSV missing column(s): {missing}")

    categories = {
        category.key: category
        for category in session.scalars(
            select(Category).where(Category.restaurant_id == restaurant_id)
        )
    }

    rows: list[schemas.ReconcileRow] = []
    total_pos = 0.0
    total_net = 0
    for line_no, raw in enumerate(reader, start=2):
        record = {(k or "").strip().lower(): (v or "").strip() for k, v in raw.items()}
        key = record.get("category_key", "")
        if not key:
            raise HTTPException(status_code=422, detail=f"row {line_no}: empty category_key")
        try:
            quantity = float(record.get("quantity", ""))
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail=f"row {line_no}: non-numeric quantity"
            ) from exc
        day = _parse_date(record["date"]) if record.get("date") else default_day
        day_from, day_to = local_day_bounds(tz_name, day)

        category = categories.get(key)
        counted_out = counted_in = 0
        if category is not None:
            stmt = select(Event.direction).where(
                Event.restaurant_id == restaurant_id,
                Event.is_canonical.is_(True),
                Event.category_id == category.id,
                Event.ts >= day_from,
                Event.ts < day_to,
            )
            for (direction,) in session.execute(stmt):
                if direction == "out":
                    counted_out += 1
                else:
                    counted_in += 1
        counted_net = counted_out - counted_in
        variance = counted_net - quantity
        variance_pct = round(variance / quantity * 100.0, 2) if quantity else None
        rows.append(
            schemas.ReconcileRow(
                category_key=key,
                category_id=category.id if category is not None else None,
                date=day.isoformat(),
                pos_quantity=quantity,
                counted_out=counted_out,
                counted_in=counted_in,
                counted_net=counted_net,
                variance=variance,
                variance_pct=variance_pct,
                unknown_category=category is None,
            )
        )
        total_pos += quantity
        total_net += counted_net

    run = ReconcileRun(
        id=new_event_id(),  # ULID: unique + creation-ordered
        restaurant_id=restaurant_id,
        date=default_day.isoformat(),
        uploaded_filename=file.filename,
        rows_json=json.dumps([row.model_dump() for row in rows], sort_keys=True),
        total_pos_quantity=total_pos,
        total_counted_net=total_net,
        created_ts=int(time.time() * 1000),
    )
    session.add(run)
    session.commit()

    return schemas.ReconcileReport(
        restaurant_id=restaurant_id,
        default_date=default_day.isoformat(),
        timezone=tz_name,
        rows=rows,
        total_pos_quantity=total_pos,
        total_counted_net=total_net,
        run_id=run.id,
    )


@router.get(
    "/restaurants/{restaurant_id}/reconcile-runs",
    response_model=schemas.Page[schemas.ReconcileRunRead],
)
def list_reconcile_runs(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    """Persisted reconciliation reports, newest first."""
    restaurant_or_404(session, restaurant_id)
    base = select(ReconcileRun).where(ReconcileRun.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    runs = session.scalars(
        base.order_by(ReconcileRun.created_ts.desc(), ReconcileRun.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    items = [
        schemas.ReconcileRunRead(
            id=run.id,
            restaurant_id=run.restaurant_id,
            date=run.date,
            uploaded_filename=run.uploaded_filename,
            rows=[schemas.ReconcileRow(**row) for row in json.loads(run.rows_json)],
            total_pos_quantity=run.total_pos_quantity,
            total_counted_net=run.total_counted_net,
            created_ts=run.created_ts,
        )
        for run in runs
    ]
    return {"items": items, "total": total, "limit": page.limit, "offset": page.offset}
