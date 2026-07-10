"""REST routers mounted under ``/api``."""

from __future__ import annotations

from fastapi import APIRouter

from toskana.api.routes import (
    cameras,
    categories,
    events,
    exit_groups,
    lines,
    mappings,
    menu_items,
    models_registry,
    reconcile,
    restaurants,
    sessions,
    stats,
    stream,
    system,
)

api_router = APIRouter()
api_router.include_router(restaurants.router)
api_router.include_router(cameras.router)
api_router.include_router(exit_groups.router)
api_router.include_router(lines.router)
api_router.include_router(categories.router)
api_router.include_router(menu_items.router)
api_router.include_router(mappings.router)
api_router.include_router(models_registry.router)
api_router.include_router(events.router)
api_router.include_router(stats.router)
api_router.include_router(sessions.router)
api_router.include_router(reconcile.router)
api_router.include_router(stream.router)
api_router.include_router(system.router)
