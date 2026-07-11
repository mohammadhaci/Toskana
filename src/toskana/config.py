"""Application configuration.

Configuration is loaded from a YAML file (path taken from the
``TOSKANA_CONFIG`` environment variable, falling back to ``./config.yaml``)
and can be overridden per-field via environment variables prefixed with
``TOSKANA_`` (e.g. ``TOSKANA_PORT=9000``).

Precedence (highest first): environment variables > YAML file > defaults.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from pydantic import field_validator
from pydantic_settings import (
    BaseSettings,
    PydanticBaseSettingsSource,
    SettingsConfigDict,
)

DEFAULT_CONFIG_FILENAME = "config.yaml"
CONFIG_PATH_ENV_VAR = "TOSKANA_CONFIG"


class AppConfig(BaseSettings):
    """Runtime configuration for a single Toskana site."""

    model_config = SettingsConfigDict(env_prefix="TOSKANA_", extra="ignore")

    active_restaurant_slug: str = "toskana"
    host: str = "127.0.0.1"
    port: int = 8420
    # Optional bearer token protecting the API (env TOSKANA_API_TOKEN).
    # None (default) = open LAN mode; set it before exposing the dashboard
    # beyond a trusted network (see docs/deployment.md, Security).
    api_token: str | None = None
    # Optional webhook receiving JSON alerts (camera down / drift) via POST.
    alert_webhook_url: str | None = None
    db_path: str = "./toskana.db"
    snapshots_dir: str = "./data/snapshots"
    snapshot_retention_days: int = 30
    # Video-analysis uploads (dashboard "Analyse" page) land here; downloads
    # from URLs (yt-dlp) too. Uploads larger than max_upload_bytes are rejected.
    uploads_dir: str = "./data/uploads"
    max_upload_bytes: int = 2 * 1024**3  # 2 GiB
    max_url_video_seconds: int = 3600  # URL analysis: refuse videos longer than 1 h
    device: str = "auto"  # auto | cpu | cuda | mps
    log_level: str = "INFO"
    # Global detector default: 'auto' resolves to yolo when ultralytics is
    # installed, else synthetic. Per-camera detector_backend overrides this
    # (seeded demo file-cameras are pinned to synthetic).
    detector_backend: str = "auto"  # auto | synthetic | yolo
    loop_file_sources: bool = True  # demo mode: file cameras loop (paced) forever
    # Hardware tuning for the YOLO backend: cpu (imgsz 480, every 2nd frame),
    # gpu (imgsz 640, every frame) or auto (gpu when CUDA is available).
    performance_preset: str = "auto"  # cpu | gpu | auto
    # AI Event Refiner: after each counted crossing, send the event snapshot
    # (cropped to the item) to a vision LLM that verifies/corrects the
    # category and optionally names the exact menu item. See docs/refiner.md.
    refiner_provider: str = "off"  # off | anthropic | openai_compatible
    # anthropic: Claude model id; openai_compatible: e.g. "qwen2.5vl" (Ollama)
    # or the model id shown by LM Studio's local server.
    refiner_model: str = "claude-haiku-4-5"
    # openai_compatible only: Ollama http://localhost:11434/v1,
    # LM Studio http://localhost:1234/v1.
    refiner_base_url: str = "http://localhost:11434/v1"
    # anthropic: falls back to the ANTHROPIC_API_KEY env var when unset;
    # openai_compatible: optional bearer token (local servers need none).
    refiner_api_key: str | None = None
    # Only refine events whose detector confidence is below this threshold.
    # 1.0 (default) refines everything; lowering it (e.g. 0.65) cuts cost by
    # only double-checking uncertain detections.
    refiner_only_below_confidence: float = 1.0
    # Also offer the restaurant's menu item names so the LLM can identify
    # the exact dish/drink (Phase 2), not just the category.
    refiner_match_menu_items: bool = True
    # Rate limit for LLM calls; excess events wait in a small queue.
    refiner_max_per_minute: int = 30

    @field_validator("performance_preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        if value.strip().lower() not in ("cpu", "gpu", "auto"):
            raise ValueError(f"performance_preset must be cpu, gpu or auto: {value!r}")
        return value.strip().lower()

    @field_validator("refiner_provider")
    @classmethod
    def _known_refiner_provider(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in ("off", "anthropic", "openai_compatible"):
            raise ValueError(
                f"refiner_provider must be off, anthropic or openai_compatible: {value!r}"
            )
        return normalized

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        # Environment variables override values passed at init time
        # (init values come from the YAML file in load_config()).
        return (env_settings, init_settings)

    @property
    def db_url(self) -> str:
        """SQLAlchemy URL for the configured SQLite database."""
        return sqlite_url(self.db_path)


def sqlite_url(db_path: str | os.PathLike[str]) -> str:
    """Build a SQLAlchemy SQLite URL from a filesystem path (or ':memory:')."""
    path = str(db_path)
    if path == ":memory:":
        return "sqlite+pysqlite:///:memory:"
    return f"sqlite+pysqlite:///{Path(path)}"


def resolve_config_path(path: str | os.PathLike[str] | None = None) -> Path:
    """Determine the YAML config file path (explicit arg > env var > default)."""
    if path is not None:
        return Path(path)
    return Path(os.environ.get(CONFIG_PATH_ENV_VAR, DEFAULT_CONFIG_FILENAME))


def load_config(path: str | os.PathLike[str] | None = None) -> AppConfig:
    """Load configuration from YAML (if present) with env-var overrides.

    A missing YAML file is not an error: defaults + env vars apply.
    An explicitly given path that does not exist raises ``FileNotFoundError``.
    """
    cfg_path = resolve_config_path(path)
    data: dict[str, Any] = {}
    if cfg_path.is_file():
        loaded = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        if loaded is not None:
            if not isinstance(loaded, dict):
                raise ValueError(f"Config file {cfg_path} must contain a YAML mapping")
            data = loaded
    elif path is not None:
        raise FileNotFoundError(f"Config file not found: {cfg_path}")
    return AppConfig(**data)
