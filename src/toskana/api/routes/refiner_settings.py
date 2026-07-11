"""Dashboard-managed AI Event Refiner settings (``/api/settings/refiner``).

* ``GET`` — the effective settings (dashboard row overrides ``config.yaml``)
  with the API key masked: only ``has_api_key`` is returned, never the key.
* ``PUT`` — persist new settings to the ``app_settings`` table and apply
  them to the running :class:`~toskana.refiner_engine.RefinerEngine`
  immediately (no restart). ``api_key`` absent/None keeps the stored key,
  an empty string clears it.
* ``POST /test`` — probe the *posted* values (not the saved ones): build a
  temporary backend, draw a small synthetic test image (a coffee-cup-ish
  circle on a plate) and ask the model to classify it against the active
  restaurant's categories. Like the camera test-source probe, failures are
  results (always HTTP 200, ``ok: false`` + friendly error) and the call
  runs in a worker thread with a hard time budget.

API keys are never echoed back and never logged.
"""

from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError

import cv2
import httpx
import numpy as np
from fastapi import APIRouter
from sqlalchemy import select
from sqlalchemy.orm import Session

from toskana.api import schemas
from toskana.api.deps import ConfigDep, RefinerDep, SessionDep
from toskana.config import AppConfig
from toskana.db.models import Category, MenuItem, Restaurant
from toskana.refiner import (
    AnthropicBackend,
    OpenAICompatBackend,
    RefinerBackend,
    RefinerCategory,
    RefinerError,
    RefinerResult,
)
from toskana.refiner_engine import PROVIDER_ANTHROPIC, PROVIDER_OFF
from toskana.settings_store import (
    RefinerSettings,
    effective_refiner_settings_for_session,
    save_refiner_settings,
)

router = APIRouter(prefix="/settings/refiner", tags=["settings"])

#: Overall connection-test budget in seconds (patched in tests).
TEST_TIMEOUT_S = 30.0

#: Test-only transport injection point (httpx.MockTransport in tests).
TRANSPORT: httpx.BaseTransport | None = None

_NO_API_KEY_ERROR = (
    "No API key available. Enter an Anthropic API key (or set the "
    "ANTHROPIC_API_KEY environment variable on the server)."
)

_PROVIDER_OFF_ERROR = "The refiner is set to 'off' — choose a provider to test the connection."

_TIMEOUT_ERROR = (
    "The connection test timed out after {budget:.0f}s. The server may be "
    "unreachable or the model is still loading — check the address/model and try again."
)

#: Categories offered when the active restaurant has none yet.
_FALLBACK_CATEGORIES = [
    RefinerCategory(key="drink", name="Drink (Getränk)"),
    RefinerCategory(key="main", name="Main dish (Hauptgericht)"),
    RefinerCategory(key="dessert", name="Dessert (Nachspeise)"),
]


def _masked(settings: RefinerSettings) -> schemas.RefinerSettingsRead:
    return schemas.RefinerSettingsRead(
        provider=settings.provider,
        model=settings.model,
        base_url=settings.base_url,
        has_api_key=bool(settings.api_key),
        only_below_confidence=settings.only_below_confidence,
        match_menu_items=settings.match_menu_items,
        max_per_minute=settings.max_per_minute,
    )


@router.get("", response_model=schemas.RefinerSettingsRead)
def read_refiner_settings(session: SessionDep, config: ConfigDep) -> schemas.RefinerSettingsRead:
    """The effective settings (dashboard row if saved, else config.yaml)."""
    return _masked(effective_refiner_settings_for_session(session, config))


@router.put("", response_model=schemas.RefinerSettingsRead)
def update_refiner_settings(
    body: schemas.RefinerSettingsUpdate,
    session: SessionDep,
    config: ConfigDep,
    refiner: RefinerDep,
) -> schemas.RefinerSettingsRead:
    """Persist + apply immediately (engine backend swap, no restart)."""
    current = effective_refiner_settings_for_session(session, config)
    settings = RefinerSettings(
        provider=body.provider,
        model=body.model,
        base_url=body.base_url,
        api_key=_resolved_key(body.api_key, current, use_saved=True),
        only_below_confidence=body.only_below_confidence,
        match_menu_items=body.match_menu_items,
        max_per_minute=body.max_per_minute,
    )
    save_refiner_settings(session, settings)
    session.commit()
    refiner.reconfigure(settings)
    return _masked(settings)


def _resolved_key(
    posted_key: str | None, current: RefinerSettings, *, use_saved: bool
) -> str | None:
    """Key semantics shared by PUT and /test: absent/None = saved key
    (when allowed), empty string = no key, anything else = the posted key."""
    if posted_key is None:
        return current.api_key if use_saved else None
    return posted_key or None  # "" clears the key


def _test_image_jpeg() -> bytes:
    """A small in-memory test scene: a coffee-cup-ish circle on a plate."""
    frame = np.full((240, 320, 3), (184, 209, 226), dtype=np.uint8)  # warm table
    cv2.ellipse(frame, (160, 130), (120, 80), 0, 0, 360, (245, 245, 245), -1)  # plate
    cv2.ellipse(frame, (160, 130), (120, 80), 0, 0, 360, (180, 180, 180), 2)
    cv2.circle(frame, (160, 125), 48, (30, 60, 120), -1)  # brown cup body
    cv2.circle(frame, (160, 125), 34, (16, 32, 64), -1)  # dark coffee surface
    cv2.ellipse(frame, (218, 125), (18, 26), 0, -70, 70, (30, 60, 120), 10)  # handle
    ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    if not ok:  # pragma: no cover - imencode on a valid array does not fail
        raise RuntimeError("could not encode the refiner test image")
    return bytes(buffer.tobytes())


def _build_test_backend(settings: RefinerSettings) -> RefinerBackend | None:
    """A throwaway backend from the posted values (transport injectable)."""
    if settings.provider == PROVIDER_ANTHROPIC:
        api_key = settings.api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        return AnthropicBackend(
            settings.model, api_key=api_key, timeout_s=TEST_TIMEOUT_S, transport=TRANSPORT
        )
    return OpenAICompatBackend(
        settings.model,
        base_url=settings.base_url,
        api_key=settings.api_key,
        timeout_s=TEST_TIMEOUT_S,
        transport=TRANSPORT,
    )


def _test_catalog(
    session: Session, config: AppConfig, *, with_menu: bool
) -> tuple[list[RefinerCategory], list[str]]:
    """The active restaurant's categories/menu names (with fallbacks)."""
    restaurant = session.scalar(
        select(Restaurant).where(Restaurant.slug == config.active_restaurant_slug)
    )
    if restaurant is None:
        return list(_FALLBACK_CATEGORIES), []
    rows = session.scalars(
        select(Category)
        .where(Category.restaurant_id == restaurant.id)
        .order_by(Category.sort_order, Category.id)
    ).all()
    categories = [RefinerCategory(key=row.key, name=row.name_en) for row in rows]
    if not categories:
        categories = list(_FALLBACK_CATEGORIES)
    menu_items: list[str] = []
    if with_menu:
        menu_items = list(
            session.scalars(
                select(MenuItem.name).where(
                    MenuItem.restaurant_id == restaurant.id, MenuItem.is_active.is_(True)
                )
            ).all()
        )
    return categories, menu_items


@router.post("/test", response_model=schemas.RefinerTestResult)
def test_refiner_settings(
    body: schemas.RefinerSettingsTest, session: SessionDep, config: ConfigDep
) -> schemas.RefinerTestResult:
    """Probe the posted settings against a generated test image."""
    if body.provider == PROVIDER_OFF:
        return schemas.RefinerTestResult(ok=False, error=_PROVIDER_OFF_ERROR)

    current = effective_refiner_settings_for_session(session, config)
    settings = RefinerSettings(
        provider=body.provider,
        model=body.model,
        base_url=body.base_url,
        api_key=_resolved_key(body.api_key, current, use_saved=body.use_saved_api_key),
        only_below_confidence=body.only_below_confidence,
        match_menu_items=body.match_menu_items,
        max_per_minute=body.max_per_minute,
    )
    backend = _build_test_backend(settings)
    if backend is None:
        return schemas.RefinerTestResult(ok=False, error=_NO_API_KEY_ERROR)

    categories, menu_items = _test_catalog(session, config, with_menu=settings.match_menu_items)
    image = _test_image_jpeg()

    def _probe() -> RefinerResult:
        try:
            return backend.refine(image, categories, menu_items)
        finally:
            close = getattr(backend, "close", None)
            if callable(close):
                close()

    # One throwaway worker per test (like the camera test-source probe): a
    # hung backend must not stall the request beyond the budget; on timeout
    # the abandoned thread finishes (and closes the client) on its own.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refiner-test")
    started = time.monotonic()
    try:
        future = executor.submit(_probe)
        try:
            result = future.result(timeout=TEST_TIMEOUT_S)
        except FutureTimeoutError:
            return schemas.RefinerTestResult(
                ok=False, error=_TIMEOUT_ERROR.format(budget=TEST_TIMEOUT_S)
            )
        except RefinerError as exc:
            return schemas.RefinerTestResult(ok=False, error=str(exc))
    finally:
        executor.shutdown(wait=False)

    latency_ms = int((time.monotonic() - started) * 1000)
    return schemas.RefinerTestResult(
        ok=True,
        latency_ms=latency_ms,
        reply=schemas.RefinerTestReply(
            category_key=result.category_key,
            menu_item_name=result.menu_item_name,
            confidence=result.confidence,
            is_item=result.is_item,
        ),
    )
