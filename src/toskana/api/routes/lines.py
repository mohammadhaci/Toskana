"""CRUD for counting lines, nested under a camera.

Mutations trigger :meth:`PipelineManager.reload_camera` so a changed line
takes effect in a running pipeline without an app restart.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import ManagerDep, PageDep, SessionDep, camera_or_404, line_or_404
from toskana.db.models import Line

router = APIRouter(prefix="/cameras/{camera_id}/lines", tags=["lines"])


@router.get("", response_model=schemas.Page[schemas.LineRead])
def list_lines(camera_id: int, session: SessionDep, page: PageDep) -> dict:
    camera_or_404(session, camera_id)
    base = select(Line).where(Line.camera_id == camera_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(Line.id).limit(page.limit).offset(page.offset)).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.LineRead, status_code=201)
def create_line(
    camera_id: int, body: schemas.LineCreate, session: SessionDep, manager: ManagerDep
) -> Line:
    camera = camera_or_404(session, camera_id)
    line = Line(restaurant_id=camera.restaurant_id, camera_id=camera_id, **body.model_dump())
    session.add(line)
    session.commit()
    manager.reload_camera(camera_id)
    return line


@router.get("/{line_id}", response_model=schemas.LineRead)
def get_line(camera_id: int, line_id: int, session: SessionDep) -> Line:
    camera_or_404(session, camera_id)
    return line_or_404(session, line_id, camera_id)


@router.put("/{line_id}", response_model=schemas.LineRead)
def update_line(
    camera_id: int,
    line_id: int,
    body: schemas.LineUpdate,
    session: SessionDep,
    manager: ManagerDep,
) -> Line:
    camera_or_404(session, camera_id)
    line = line_or_404(session, line_id, camera_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(line, key, value)
    session.commit()
    manager.reload_camera(camera_id)  # hot reload: new geometry applies live
    return line


@router.delete("/{line_id}", status_code=204)
def delete_line(camera_id: int, line_id: int, session: SessionDep, manager: ManagerDep) -> None:
    camera_or_404(session, camera_id)
    line = line_or_404(session, line_id, camera_id)
    session.delete(line)
    session.commit()
    manager.reload_camera(camera_id)
