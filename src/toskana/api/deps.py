"""FastAPI dependencies: DB sessions, app services, tenant-scoped lookups.

Tenant isolation contract: every nested lookup filters by the restaurant id
from the URL; an entity that exists but belongs to another restaurant is
indistinguishable from a missing one (404). Cross-tenant *references* in
request bodies (e.g. a camera pointing at another restaurant's exit group)
are also rejected with 404.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from toskana.config import AppConfig
from toskana.db.models import Camera, Category, ExitGroup, Line, ModelRegistry, Restaurant
from toskana.events.bus import EventBus
from toskana.vision.manager import PipelineManager


def get_config(request: Request) -> AppConfig:
    return request.app.state.config  # type: ignore[no-any-return]


def get_bus(request: Request) -> EventBus:
    return request.app.state.bus  # type: ignore[no-any-return]


def get_manager(request: Request) -> PipelineManager:
    return request.app.state.manager  # type: ignore[no-any-return]


def get_session(request: Request) -> Iterator[Session]:
    factory = request.app.state.session_factory
    with factory() as session:
        yield session


SessionDep = Annotated[Session, Depends(get_session)]
ConfigDep = Annotated[AppConfig, Depends(get_config)]
BusDep = Annotated[EventBus, Depends(get_bus)]
ManagerDep = Annotated[PipelineManager, Depends(get_manager)]


@dataclass(frozen=True)
class PageParams:
    limit: int
    offset: int


def page_params(
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> PageParams:
    return PageParams(limit=limit, offset=offset)


PageDep = Annotated[PageParams, Depends(page_params)]


# -- tenant-scoped lookups (404 on missing OR foreign-tenant) --------------------


def restaurant_or_404(session: Session, restaurant_id: int) -> Restaurant:
    restaurant = session.get(Restaurant, restaurant_id)
    if restaurant is None:
        raise HTTPException(status_code=404, detail="restaurant not found")
    return restaurant


def camera_or_404(session: Session, camera_id: int, restaurant_id: int | None = None) -> Camera:
    camera = session.get(Camera, camera_id)
    if camera is None or (restaurant_id is not None and camera.restaurant_id != restaurant_id):
        raise HTTPException(status_code=404, detail="camera not found")
    return camera


def line_or_404(session: Session, line_id: int, camera_id: int | None = None) -> Line:
    line = session.get(Line, line_id)
    if line is None or (camera_id is not None and line.camera_id != camera_id):
        raise HTTPException(status_code=404, detail="line not found")
    return line


def exit_group_or_404(session: Session, exit_group_id: int, restaurant_id: int) -> ExitGroup:
    group = session.get(ExitGroup, exit_group_id)
    if group is None or group.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="exit group not found")
    return group


def category_or_404(session: Session, category_id: int, restaurant_id: int) -> Category:
    category = session.get(Category, category_id)
    if category is None or category.restaurant_id != restaurant_id:
        raise HTTPException(status_code=404, detail="category not found")
    return category


def model_or_404(session: Session, model_id: int, restaurant_id: int) -> ModelRegistry:
    """A model visible to the restaurant: its own, or a shared (NULL owner) one."""
    model = session.get(ModelRegistry, model_id)
    if model is None or model.restaurant_id not in (None, restaurant_id):
        raise HTTPException(status_code=404, detail="model not found")
    return model
