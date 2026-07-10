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

from toskana import __version__
from toskana.api import ws as ws_module
from toskana.api.routes import api_router
from toskana.api.ws import LiveBroadcaster
from toskana.config import AppConfig
from toskana.events.bus import EventBus
from toskana.events.writer import EventWriter, make_writer_session_factory
from toskana.vision.dedup import build_dedup_engine
from toskana.vision.manager import PipelineManager

logger = logging.getLogger(__name__)

#: Local dev origins (Vite dev server etc.); the built dashboard is
#: same-origin and needs no CORS.
CORS_ORIGIN_REGEX = r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$"


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

        dedup = await asyncio.to_thread(
            build_dedup_engine, session_factory, config.active_restaurant_slug, bus=bus
        )

        app.state.session_factory = session_factory
        app.state.bus = bus
        app.state.writer = writer
        app.state.manager = manager
        app.state.broadcaster = broadcaster
        app.state.dedup = dedup

        writer.start()
        if dedup is not None:
            dedup.start()  # after writer.start(): inserts queue before demotions
        broadcaster.start(asyncio.get_running_loop())
        if start_pipelines:
            started = await asyncio.to_thread(manager.start_all)
            logger.info("pipeline manager started %d camera(s)", started)
        try:
            yield
        finally:
            await asyncio.to_thread(manager.stop_all)
            if dedup is not None:
                dedup.stop()
            broadcaster.stop()
            await asyncio.to_thread(writer.stop)

    app = FastAPI(title="Toskana", version=__version__, lifespan=lifespan)
    app.state.config = config

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
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="dashboard")
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
