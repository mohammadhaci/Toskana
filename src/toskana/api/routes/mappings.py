"""CRUD for class mappings + atomic bulk replace of a model's mapping set."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import delete, func, select

from toskana.api import schemas
from toskana.api.deps import (
    ManagerDep,
    PageDep,
    SessionDep,
    category_or_404,
    model_or_404,
    restaurant_or_404,
)
from toskana.db.models import ClassMapping, MenuItem

router = APIRouter(prefix="/restaurants/{restaurant_id}/mappings", tags=["mappings"])


def _mapping_or_404(session: SessionDep, mapping_id: int, restaurant_id: int) -> ClassMapping:
    mapping = session.get(ClassMapping, mapping_id)
    if mapping is None or mapping.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="mapping not found")
    return mapping


def _check_targets(
    session: SessionDep,
    restaurant_id: int,
    category_id: int | None,
    menu_item_id: int | None,
) -> None:
    if category_id is not None:
        category_or_404(session, category_id, restaurant_id)
    if menu_item_id is not None:
        item = session.get(MenuItem, menu_item_id)
        if item is None or item.restaurant_id != restaurant_id:
            raise HTTPException(status_code=404, detail="menu item not found")


@router.get("", response_model=schemas.Page[schemas.MappingRead])
def list_mappings(
    restaurant_id: int,
    session: SessionDep,
    page: PageDep,
    model_id: int | None = None,
) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = select(ClassMapping).where(ClassMapping.restaurant_id == restaurant_id)
    if model_id is not None:
        base = base.where(ClassMapping.model_id == model_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(ClassMapping.id).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.MappingRead, status_code=201)
def create_mapping(
    restaurant_id: int, body: schemas.MappingCreate, session: SessionDep, manager: ManagerDep
) -> ClassMapping:
    restaurant_or_404(session, restaurant_id)
    model_or_404(session, body.model_id, restaurant_id)
    _check_targets(session, restaurant_id, body.category_id, body.menu_item_id)
    duplicate = session.scalar(
        select(ClassMapping).where(
            ClassMapping.restaurant_id == restaurant_id,
            ClassMapping.model_id == body.model_id,
            ClassMapping.model_class_id == body.model_class_id,
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail=f"mapping for model class {body.model_class_id} already exists",
        )
    mapping = ClassMapping(restaurant_id=restaurant_id, **body.model_dump())
    session.add(mapping)
    session.commit()
    manager.reload_restaurant(restaurant_id)
    return mapping


@router.put("/model/{model_id}", response_model=schemas.Page[schemas.MappingRead])
def replace_model_mapping_set(
    restaurant_id: int,
    model_id: int,
    body: list[schemas.MappingBulkItem],
    session: SessionDep,
    manager: ManagerDep,
) -> dict:
    """Atomically replace the whole mapping set of one model for this restaurant."""
    restaurant_or_404(session, restaurant_id)
    model_or_404(session, model_id, restaurant_id)
    seen: set[int] = set()
    for item in body:
        if item.model_class_id in seen:
            raise HTTPException(
                status_code=422, detail=f"duplicate model_class_id {item.model_class_id} in set"
            )
        seen.add(item.model_class_id)
        _check_targets(session, restaurant_id, item.category_id, item.menu_item_id)
    session.execute(
        delete(ClassMapping).where(
            ClassMapping.restaurant_id == restaurant_id, ClassMapping.model_id == model_id
        )
    )
    rows = [
        ClassMapping(restaurant_id=restaurant_id, model_id=model_id, **item.model_dump())
        for item in body
    ]
    session.add_all(rows)
    session.commit()
    manager.reload_restaurant(restaurant_id)
    return {"items": rows, "total": len(rows), "limit": len(rows) or 1, "offset": 0}


@router.get("/{mapping_id}", response_model=schemas.MappingRead)
def get_mapping(restaurant_id: int, mapping_id: int, session: SessionDep) -> ClassMapping:
    restaurant_or_404(session, restaurant_id)
    return _mapping_or_404(session, mapping_id, restaurant_id)


@router.put("/{mapping_id}", response_model=schemas.MappingRead)
def update_mapping(
    restaurant_id: int,
    mapping_id: int,
    body: schemas.MappingUpdate,
    session: SessionDep,
    manager: ManagerDep,
) -> ClassMapping:
    restaurant_or_404(session, restaurant_id)
    mapping = _mapping_or_404(session, mapping_id, restaurant_id)
    fields = body.model_dump(exclude_unset=True)
    _check_targets(session, restaurant_id, fields.get("category_id"), fields.get("menu_item_id"))
    new_category = fields.get("category_id", mapping.category_id)
    new_menu_item = fields.get("menu_item_id", mapping.menu_item_id)
    if new_category is None and new_menu_item is None:
        raise HTTPException(status_code=422, detail="mapping needs category_id or menu_item_id")
    for key, value in fields.items():
        setattr(mapping, key, value)
    session.commit()
    manager.reload_restaurant(restaurant_id)
    return mapping


@router.delete("/{mapping_id}", status_code=204)
def delete_mapping(
    restaurant_id: int, mapping_id: int, session: SessionDep, manager: ManagerDep
) -> None:
    restaurant_or_404(session, restaurant_id)
    mapping = _mapping_or_404(session, mapping_id, restaurant_id)
    session.delete(mapping)
    session.commit()
    manager.reload_restaurant(restaurant_id)
