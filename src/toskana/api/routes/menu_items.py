"""CRUD for Phase-2 menu items per restaurant."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, category_or_404, restaurant_or_404
from toskana.db.models import MenuItem

router = APIRouter(prefix="/restaurants/{restaurant_id}/menu-items", tags=["menu-items"])


def _menu_item_or_404(session: SessionDep, item_id: int, restaurant_id: int) -> MenuItem:
    item = session.get(MenuItem, item_id)
    if item is None or item.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="menu item not found")
    return item


@router.get("", response_model=schemas.Page[schemas.MenuItemRead])
def list_menu_items(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(MenuItem).where(MenuItem.restaurant_id == restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(base.order_by(MenuItem.id).limit(page.limit).offset(page.offset)).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.MenuItemRead, status_code=201)
def create_menu_item(
    restaurant_id: int, body: schemas.MenuItemCreate, session: SessionDep
) -> MenuItem:
    restaurant_or_404(session, restaurant_id)
    category_or_404(session, body.category_id, restaurant_id)  # cross-tenant reference -> 404
    duplicate = session.scalar(
        select(MenuItem).where(MenuItem.restaurant_id == restaurant_id, MenuItem.name == body.name)
    )
    if duplicate is not None:
        raise HTTPException(status_code=409, detail=f"menu item {body.name!r} already exists")
    item = MenuItem(restaurant_id=restaurant_id, **body.model_dump())
    session.add(item)
    session.commit()
    return item


@router.get("/{item_id}", response_model=schemas.MenuItemRead)
def get_menu_item(restaurant_id: int, item_id: int, session: SessionDep) -> MenuItem:
    restaurant_or_404(session, restaurant_id)
    return _menu_item_or_404(session, item_id, restaurant_id)


@router.put("/{item_id}", response_model=schemas.MenuItemRead)
def update_menu_item(
    restaurant_id: int, item_id: int, body: schemas.MenuItemUpdate, session: SessionDep
) -> MenuItem:
    restaurant_or_404(session, restaurant_id)
    item = _menu_item_or_404(session, item_id, restaurant_id)
    fields = body.model_dump(exclude_unset=True)
    if fields.get("category_id") is not None:
        category_or_404(session, fields["category_id"], restaurant_id)
    for key, value in fields.items():
        setattr(item, key, value)
    session.commit()
    return item


@router.delete("/{item_id}", status_code=204)
def delete_menu_item(restaurant_id: int, item_id: int, session: SessionDep) -> None:
    restaurant_or_404(session, restaurant_id)
    item = _menu_item_or_404(session, item_id, restaurant_id)
    session.delete(item)
    session.commit()
