"""CRUD for restaurants (tenants)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from sqlalchemy import func, select

from toskana.api import schemas
from toskana.api.deps import PageDep, SessionDep, restaurant_or_404
from toskana.db.models import Restaurant

router = APIRouter(prefix="/restaurants", tags=["restaurants"])


@router.get("", response_model=schemas.Page[schemas.RestaurantRead])
def list_restaurants(session: SessionDep, page: PageDep) -> dict:
    total = session.scalar(select(func.count()).select_from(Restaurant)) or 0
    rows = session.scalars(
        select(Restaurant).order_by(Restaurant.id).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.post("", response_model=schemas.RestaurantRead, status_code=201)
def create_restaurant(body: schemas.RestaurantCreate, session: SessionDep) -> Restaurant:
    if session.scalar(select(Restaurant).where(Restaurant.slug == body.slug)) is not None:
        raise HTTPException(status_code=409, detail=f"slug {body.slug!r} already exists")
    restaurant = Restaurant(**body.model_dump())
    session.add(restaurant)
    session.commit()
    return restaurant


@router.get("/{restaurant_id}", response_model=schemas.RestaurantRead)
def get_restaurant(restaurant_id: int, session: SessionDep) -> Restaurant:
    return restaurant_or_404(session, restaurant_id)


@router.put("/{restaurant_id}", response_model=schemas.RestaurantRead)
def update_restaurant(
    restaurant_id: int, body: schemas.RestaurantUpdate, session: SessionDep
) -> Restaurant:
    restaurant = restaurant_or_404(session, restaurant_id)
    for key, value in body.model_dump(exclude_unset=True).items():
        setattr(restaurant, key, value)
    session.commit()
    return restaurant


@router.delete("/{restaurant_id}", status_code=204)
def delete_restaurant(restaurant_id: int, session: SessionDep) -> None:
    restaurant = restaurant_or_404(session, restaurant_id)
    session.delete(restaurant)
    session.commit()
