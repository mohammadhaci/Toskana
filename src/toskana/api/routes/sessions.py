"""CRUD for service sessions (shifts) + explicit end action."""

from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, restaurant_or_404
from toskana.db.models import ServiceSession

router = APIRouter(prefix="/restaurants/{restaurant_id}/sessions", tags=["sessions"])


def _session_or_404(session: SessionDep, session_id: int, restaurant_id: int) -> ServiceSession:
    row = session.get(ServiceSession, session_id)
    if row is None or row.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="session not found")
    return row


@router.get("", response_model=schemas.Page[schemas.SessionRead])
def list_sessions(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(ServiceSession).where(ServiceSession.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(ServiceSession.started_ts.desc(), ServiceSession.id.desc())
        .limit(page.limit)
        .offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.SessionRead, status_code=201)
def create_session(
    restaurant_id: int, body: schemas.SessionCreate, session: SessionDep
) -> ServiceSession:
    restaurant_or_404(session, restaurant_id)
    row = ServiceSession(
        restaurant_id=restaurant_id,
        name=body.name,
        started_ts=body.started_ts if body.started_ts is not None else int(time.time() * 1000),
        note=body.note,
    )
    session.add(row)
    session.commit()
    return row


@router.get("/{session_id}", response_model=schemas.SessionRead)
def get_session_row(restaurant_id: int, session_id: int, session: SessionDep) -> ServiceSession:
    restaurant_or_404(session, restaurant_id)
    return _session_or_404(session, session_id, restaurant_id)


@router.put("/{session_id}", response_model=schemas.SessionRead)
def update_session(
    restaurant_id: int, session_id: int, body: schemas.SessionUpdate, session: SessionDep
) -> ServiceSession:
    restaurant_or_404(session, restaurant_id)
    row = _session_or_404(session, session_id, restaurant_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(row, key, value)
    session.commit()
    return row


@router.post("/{session_id}/end", response_model=schemas.SessionRead)
def end_session(restaurant_id: int, session_id: int, session: SessionDep) -> ServiceSession:
    restaurant_or_404(session, restaurant_id)
    row = _session_or_404(session, session_id, restaurant_id)
    if row.ended_ts is not None:
        raise HTTPException(status_code=409, detail="session already ended")
    row.ended_ts = int(time.time() * 1000)
    session.commit()
    return row


@router.delete("/{session_id}", status_code=204)
def delete_session(restaurant_id: int, session_id: int, session: SessionDep) -> None:
    restaurant_or_404(session, restaurant_id)
    row = _session_or_404(session, session_id, restaurant_id)
    session.delete(row)
    session.commit()
