"""CRUD for Phase-1 categories per restaurant."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, category_or_404, restaurant_or_404
from toskana.db.models import Category

router = APIRouter(prefix="/restaurants/{restaurant_id}/categories", tags=["categories"])


@router.get("", response_model=schemas.Page[schemas.CategoryRead])
def list_categories(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(Category).where(Category.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(Category.sort_order, Category.id).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.CategoryRead, status_code=201)
def create_category(
    restaurant_id: int, body: schemas.CategoryCreate, session: SessionDep
) -> Category:
    restaurant_or_404(session, restaurant_id)
    duplicate = session.scalar(
        select(Category).where(Category.restaurant_id == restaurant_id, Category.key == body.key)
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail=f"category key {body.key!r} already exists")
    category = Category(restaurant_id=restaurant_id, **body.model_dump())
    session.add(category)
    session.commit()
    return category


@router.get("/{category_id}", response_model=schemas.CategoryRead)
def get_category(restaurant_id: int, category_id: int, session: SessionDep) -> Category:
    restaurant_or_404(session, restaurant_id)
    return category_or_404(session, category_id, restaurant_id)


@router.put("/{category_id}", response_model=schemas.CategoryRead)
def update_category(
    restaurant_id: int, category_id: int, body: schemas.CategoryUpdate, session: SessionDep
) -> Category:
    restaurant_or_404(session, restaurant_id)
    category = category_or_404(session, category_id, restaurant_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(category, key, value)
    session.commit()
    return category


@router.delete("/{category_id}", status_code=204)
def delete_category(restaurant_id: int, category_id: int, session: SessionDep) -> None:
    restaurant_or_404(session, restaurant_id)
    category = category_or_404(session, category_id, restaurant_id)
    session.delete(category)
    session.commit()
