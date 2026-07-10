"""System health and environment info."""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter, Request
from sqlalchemy import select, text

from toskana import __version__
from toskana.api import schemas
from toskana.api.deps import ConfigDep, ManagerDep, SessionDep
from toskana.db.models import Restaurant
from toskana.vision.presets import resolve_preset

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/health", response_model=schemas.SystemHealth)
def health(request: Request, session: SessionDep, manager: ManagerDep) -> schemas.SystemHealth:
    db_ok = True
    try:
        session.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001 - any DB failure means degraded
        db_ok = False
    writer = request.app.state.writer
    dedup = getattr(request.app.state, "dedup", None)
    dedup_stats = dedup.stats if dedup is not None else None
    pipelines = [schemas.CameraStatus(**status) for status in manager.status()]
    return schemas.SystemHealth(
        status="ok" if db_ok else "degraded",
        db_ok=db_ok,
        pipelines=pipelines,
        writer_written_events=writer.written_events if writer is not None else 0,
        writer_written_gaps=writer.written_gaps if writer is not None else 0,
        dedup_active=dedup is not None,
        dedup_matches=dedup_stats.matches if dedup_stats is not None else 0,
        dedup_demotions=dedup_stats.demotions if dedup_stats is not None else 0,
    )


@router.post("/retention/run", response_model=schemas.RetentionRunResult)
def run_retention(request: Request, config: ConfigDep) -> schemas.RetentionRunResult:
    """Run the snapshot-retention pass now (it also runs daily on its own)."""
    job = request.app.state.retention
    result = job.run_once()
    return schemas.RetentionRunResult(
        snapshot_retention_days=config.snapshot_retention_days,
        cutoff_ms=result.cutoff_ms,
        deleted_snapshots=result.deleted_snapshots,
        cleared_events=result.cleared_events,
        orphans_removed=result.orphans_removed,
        removed_dirs=result.removed_dirs,
        runs=job.runs,
    )


@router.get("/info", response_model=schemas.SystemInfo)
def info(session: SessionDep, config: ConfigDep) -> schemas.SystemInfo:
    torch_available = importlib.util.find_spec("torch") is not None
    cuda_available = False
    if torch_available:
        try:
            import torch

            cuda_available = bool(torch.cuda.is_available())
        except Exception:  # noqa: BLE001 - a broken torch install must not kill /info
            cuda_available = False

    db_file = Path(config.db_path)
    db_size = db_file.stat().st_size if db_file.is_file() else None
    preset = resolve_preset(config.performance_preset, cuda=cuda_available)

    active: Restaurant | None
    try:
        active = session.scalar(
            select(Restaurant).where(Restaurant.slug == config.active_restaurant_slug)
        )
    except Exception:  # noqa: BLE001 - missing schema must not kill /info
        active = None

    return schemas.SystemInfo(
        version=__version__,
        active_restaurant_slug=config.active_restaurant_slug,
        active_restaurant=(
            schemas.RestaurantRead.model_validate(active) if active is not None else None
        ),
        db_path=config.db_path,
        db_size_bytes=db_size,
        torch_available=torch_available,
        cuda_available=cuda_available,
        detector_backend=config.detector_backend,
        device=config.device,
        loop_file_sources=config.loop_file_sources,
        snapshots_dir=config.snapshots_dir,
        snapshot_retention_days=config.snapshot_retention_days,
        performance_preset=config.performance_preset,
        performance_preset_resolved=preset.name,
        preset_imgsz=preset.imgsz,
        preset_frame_skip=preset.frame_skip,
    )
