"""FastAPI application factory.

``create_app(config)`` wires the whole site process together:

* one shared :class:`~toskana.events.bus.EventBus`,
* an :class:`~toskana.events.writer.EventWriter` (single DB writer thread)
  subscribed to the bus,
* a :class:`~toskana.vision.manager.PipelineManager` publishing to the bus
  (auto-started unless ``start_pipelines=False`` / ``toskana run
  --no-pipeline``),
* the :class:`~toskana.api.ws.LiveBroadcaster` forwarding bus events to
  ``/ws/live`` clients,
* a :class:`~toskana.retention.RetentionJob` deleting expired event
  snapshots daily (manually triggerable via ``POST
  /api/system/retention/run``),
* the REST routers under ``/api`` and the built dashboard (``web/dist``)
  at ``/`` when present.

Event flow: ``CameraPipeline -> bus['crossing'] -> {EventWriter,
DedupEngine, LiveBroadcaster}``. The M8 DedupEngine (built only when the
active restaurant has an exit group with >= 2 cameras) subscribes *after*
the writer: events are written canonical optimistically, then demoted via a
``demote`` update the writer applies + a WS ``correction`` broadcast.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response
from starlette.types import Scope

from toskana import __version__
from toskana.alerts import AlertNotifier
from toskana.api import ws as ws_module
from toskana.api.auth import TokenAuthMiddleware
from toskana.api.routes import api_router
from toskana.api.ws import LiveBroadcaster
from toskana.config import AppConfig
from toskana.events.bus import EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory
from toskana.retention import RetentionJob
from toskana.vision.dedup import build_dedup_engine
from toskana.vision.manager import PipelineManager

logger = logging.getLogger(__name__)

#: Local dev origins (Vite dev server etc.); the built dashboard is
#: same-origin and needs no CORS.
CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


class SPAStaticFiles(StaticFiles):
    """Static files with an SPA fallback: unknown extensionless paths serve
    ``index.html`` so deep links into the dashboard (``/stats``,
    ``/admin/cameras`` …) survive a hard reload. ``/api`` and ``/ws`` routes
    are matched before this mount and are unaffected."""

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            response = await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404 and self._is_spa_route(path):
                return await super().get_response("index.html", scope)
            raise
        if response.status_code == 404 and self._is_spa_route(path):
            return await super().get_response("index.html", scope)
        return response

    @staticmethod
    def _is_spa_route(path: str) -> bool:
        """Dashboard route (extensionless, not API/WS/docs namespace)."""
        normalized = path.lstrip("/")
        if normalized.split("/", 1)[0] in ("api", "ws", "docs", "openapi.json", "redoc"):
            return False
        return "." not in normalized.rsplit("/", 1)[-1]


def find_web_dist() -> Path | None:
    """The built dashboard, if present (repo checkout or cwd)."""
    candidates = [
        Path(__file__).resolve().parents[3] / "web" / "dist",  # <root>/src/toskana/api/app.py
        Path.cwd() / "web" / "dist",
    ]
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def create_app(config: AppConfig, *, start_pipelines: bool = True) -> FastAPI:
    """Build the ASGI app; services start/stop with the lifespan."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # NullPool + check_same_thread=False: safe for FastAPI's threadpool,
        # the manager's pipeline threads and the writer thread alike.
        session_factory = make_writer_session_factory(config.db_path)
        bus = EventBus()
        writer = EventWriter(make_writer_session_factory(config.db_path), bus=bus)
        manager = PipelineManager(config, bus=bus, session_factory=session_factory)
        broadcaster = LiveBroadcaster(bus)
        retention = RetentionJob(config, session_factory)
        notifier = (
            AlertNotifier(config.alert_webhook_url, bus=bus, session_factory=session_factory)
            if config.alert_webhook_url
            else None
        )

        dedup = await asyncio.to_thread(
            build_dedup_engine, session_factory, config.active_restaurant_slug, bus=bus
        )

        app.state.session_factory = session_factory
        app.state.bus = bus
        app.state.writer = writer
        app.state.manager = manager
        app.state.broadcaster = broadcaster
        app.state.dedup = dedup
        app.state.retention = retention
        app.state.notifier = notifier

        writer.start()
        if dedup is not None:
            dedup.start()  # after writer.start(): inserts queue before demotions
        broadcaster.start(asyncio.get_running_loop())
        retention.start()  # daily snapshot-retention pass (GDPR)
        if notifier is not None:
            notifier.start()  # before pipelines: startup crashes must alert too
        if start_pipelines:
            started = await asyncio.to_thread(manager.start_all)
            logger.info("pipeline manager started %d camera(s)", started)
        try:
            yield
        finally:
            await asyncio.to_thread(manager.stop_all)
            await asyncio.to_thread(retention.stop)
            if notifier is not None:
                await asyncio.to_thread(notifier.stop)
            if dedup is not None:
                dedup.stop()
            broadcaster.stop()
            await asyncio.to_thread(writer.stop)

    app = FastAPI(title="Toskana", version=__version__, lifespan=lifespan)
    app.state.config = config

    if config.api_token:
        # Added before CORS so CORS wraps it (401s carry CORS headers in dev).
        app.add_middleware(TokenAuthMiddleware, token=config.api_token)
    app.add_middleware(
        CORSMiddleware,
        allow_origin_regex=CORS_ORIGIN_REGEX,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(api_router, prefix="/api")
    app.include_router(ws_module.router)

    web_dist = find_web_dist()
    if web_dist is not None:
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="dashboard")
    else:

        @app.get("/", include_in_schema=False)
        def index() -> JSONResponse:
            return JSONResponse(
                {
                    "message": "Toskana API is running; the dashboard is not built yet "
                    "(web/dist missing — coming with M5).",
                    "api": "/api",
                    "docs": "/docs",
                    "health": "/api/system/health",
                }
            )

    return app
