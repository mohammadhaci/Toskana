"""Dashboard-managed settings persisted in the ``app_settings`` table.

The AI Event Refiner is configured either via ``config.yaml``
(``refiner_*`` fields) or — preferred — from the dashboard, which stores a
:class:`RefinerSettings` JSON blob under the ``refiner`` key.

Precedence: a stored dashboard row **overrides** the ``config.yaml``
values entirely; without a row the config values apply. The
``ANTHROPIC_API_KEY`` environment variable stays the final fallback for
the Anthropic API key (resolved where the backend is built, never here).

API keys are stored verbatim in the local SQLite database but are never
returned by the API (only a ``has_api_key`` flag) and never logged.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.models import AppSetting

logger = logging.getLogger(__name__)

REFINER_SETTINGS_KEY = "refiner"

RefinerProvider = Literal["off", "anthropic", "openai_compatible"]


class RefinerSettings(BaseModel):
    """The 7 refiner knobs, mirroring the ``refiner_*`` config defaults."""

    provider: RefinerProvider = "off"
    model: str = Field(default="claude-haiku-4-5", min_length=1, max_length=200)
    base_url: str = Field(default="http://localhost:11434/v1", min_length=1, max_length=1000)
    api_key: str | None = None
    only_below_confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    match_menu_items: bool = True
    max_per_minute: int = Field(default=30, ge=0)


def refiner_settings_from_config(config: AppConfig) -> RefinerSettings:
    """The ``config.yaml`` (+ env override) view of the refiner settings."""
    return RefinerSettings(
        provider=config.refiner_provider,  # type: ignore[arg-type]  # validated in AppConfig
        model=config.refiner_model,
        base_url=config.refiner_base_url,
        api_key=config.refiner_api_key,
        only_below_confidence=config.refiner_only_below_confidence,
        match_menu_items=config.refiner_match_menu_items,
        max_per_minute=config.refiner_max_per_minute,
    )


def load_refiner_settings(session: Session) -> RefinerSettings | None:
    """The stored dashboard settings, or None when nothing was saved yet."""
    row = session.get(AppSetting, REFINER_SETTINGS_KEY)
    if row is None:
        return None
    try:
        return RefinerSettings.model_validate(json.loads(row.value))
    except (json.JSONDecodeError, ValidationError):
        logger.warning("stored refiner settings are invalid; falling back to config.yaml")
        return None


def save_refiner_settings(session: Session, settings: RefinerSettings) -> None:
    """Upsert the dashboard settings blob (caller commits)."""
    value = settings.model_dump_json()
    row = session.get(AppSetting, REFINER_SETTINGS_KEY)
    if row is None:
        session.add(AppSetting(key=REFINER_SETTINGS_KEY, value=value))
    else:
        row.value = value


def effective_refiner_settings_for_session(session: Session, config: AppConfig) -> RefinerSettings:
    """Dashboard row if present, else the ``config.yaml`` values."""
    stored = load_refiner_settings(session)
    return stored if stored is not None else refiner_settings_from_config(config)


def effective_refiner_settings(
    session_factory: sessionmaker[Session], config: AppConfig
) -> RefinerSettings:
    """Dashboard row if present, else the ``config.yaml`` values.

    A missing/unmigrated ``app_settings`` table (or any DB hiccup) falls
    back to the config values — settings must never take the app down.
    """
    try:
        with session_factory() as session:
            return effective_refiner_settings_for_session(session, config)
    except Exception:  # noqa: BLE001 - e.g. table missing before migration
        logger.warning("could not read app_settings; using config.yaml refiner settings")
        return refiner_settings_from_config(config)
