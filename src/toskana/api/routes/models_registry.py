"""Model registry: list models visible to a restaurant + activate one.

Activation deactivates every other model visible to that restaurant (its own
and shared pretrained ones) and hot-reloads the restaurant's running
pipelines so the new weights/mapping set take effect.
"""

from __future__ import annotations

from fastapi import APIRouter
from sqlalchemy import func, or_, select

from toskana.api import schemas
from toskana.api.deps import ManagerDep, PageDep, SessionDep, model_or_404, restaurant_or_404
from toskana.db.models import ModelRegistry

router = APIRouter(prefix="/restaurants/{restaurant_id}/models", tags=["models"])


def _visible_models(restaurant_id: int):  # noqa: ANN202
    return select(ModelRegistry).where(
        or_(
            ModelRegistry.restaurant_id == restaurant_id,
            ModelRegistry.restaurant_id.is_(None),
        )
    )


@router.get("", response_model=schemas.Page[schemas.ModelRead])
def list_models(restaurant_id: int, session: SessionDep, page: PageDep) -> dict:
    restaurant_or_404(session, restaurant_id)
    base = _visible_models(restaurant_id)
    total = session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = session.scalars(
        base.order_by(ModelRegistry.id).limit(page.limit).offset(page.offset)
    ).all()
    return {"items": rows, "total": total, "limit": page.limit, "offset": page.offset}


@router.get("/{model_id}", response_model=schemas.ModelRead)
def get_model(restaurant_id: int, model_id: int, session: SessionDep) -> ModelRegistry:
    restaurant_or_404(session, restaurant_id)
    return model_or_404(session, model_id, restaurant_id)


@router.post("/{model_id}/activate", response_model=schemas.ModelRead)
def activate_model(
    restaurant_id: int, model_id: int, session: SessionDep, manager: ManagerDep
) -> ModelRegistry:
    restaurant_or_404(session, restaurant_id)
    model = model_or_404(session, model_id, restaurant_id)
    others = session.scalars(_visible_models(restaurant_id)).all()
    for row in others:
        row.is_active = row.id == model.id
    session.commit()
    manager.reload_restaurant(restaurant_id)  # swap weights + mapping set atomically
    return model
