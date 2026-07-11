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

    @field_validator("performance_preset")
    @classmethod
    def _known_preset(cls, value: str) -> str:
        if value.strip().lower() not in ("cpu", "gpu", "auto"):
            raise ValueError(f"performance_preset must be cpu, gpu or auto: {value!r}")
        return value.strip().lower()

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
