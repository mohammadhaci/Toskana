"""CRUD for exit groups (dedup scope: window + strategy + primary camera)."""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, exit_group_or_404, restaurant_or_404
from toskana.db.models import ExitGroup

router = APIRouter(prefix="/restaurants/{restaurant_id}/exit-groups", tags=["exit-groups"])


@router.get("", response_model=schemas.Page[schemas.ExitGroupRead])
def list_exit_groups(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(ExitGroup).where(ExitGroup.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(ExitGroup.id).limit(page.limit).offset(page.offset)).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.ExitGroupRead, status_code=201)
def create_exit_group(
    restaurant_id: int, body: schemas.ExitGroupCreate, session: SessionDep
) -> ExitGroup:
    restaurant_or_404(session, restaurant_id)
    group = ExitGroup(restaurant_id=restaurant_id, **body.model_dump())
    session.add(group)
    session.commit()
    return group


@router.get("/{exit_group_id}", response_model=schemas.ExitGroupRead)
def get_exit_group(restaurant_id: int, exit_group_id: int, session: SessionDep) -> ExitGroup:
    restaurant_or_404(session, restaurant_id)
    return exit_group_or_404(session, exit_group_id, restaurant_id)


@router.put("/{exit_group_id}", response_model=schemas.ExitGroupRead)
def update_exit_group(
    restaurant_id: int, exit_group_id: int, body: schemas.ExitGroupUpdate, session: SessionDep
) -> ExitGroup:
    restaurant_or_404(session, restaurant_id)
    group = exit_group_or_404(session, exit_group_id, restaurant_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(group, key, value)
    session.commit()
    return group


@router.delete("/{exit_group_id}", status_code=204)
def delete_exit_group(restaurant_id: int, exit_group_id: int, session: SessionDep) -> None:
    restaurant_or_404(session, restaurant_id)
    group = exit_group_or_404(session, exit_group_id, restaurant_id)
    session.delete(group)
    session.commit()
