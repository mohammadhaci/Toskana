"""GDPR retention: delete event snapshots older than the configured window.

Event **snapshots** are images of the pass area (they may show staff) and
are therefore deleted after ``snapshot_retention_days``. The **event rows**
stay: category/direction/timestamp metadata contains no personal data and
is what statistics and POS reconciliation are built on (see
``docs/privacy.md``).

:func:`cleanup_snapshots` — one pass, idempotent:

1. every event older than the cutoff with a ``snapshot_path`` gets its file
   deleted and the column set to NULL;
2. snapshot files on disk that no event references (orphans — e.g. left
   over after a crashed write) are deleted once *they* are older than the
   cutoff (mtime), so files racing an in-flight event insert survive;
3. empty per-day directories are removed.

The ``calibration/`` subdirectory (drift-detection references, not personal
data) is never touched.

Scheduling: :class:`RetentionJob` runs the cleanup daily on a background
thread (started in the app lifespan); ``toskana cleanup`` runs the same pass
once for cron/manual use.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.models import Event

logger = logging.getLogger(__name__)

_DAY_MS = 86_400_000
_SNAPSHOT_SUFFIXES = {".jpg", ".jpeg", ".png"}
_CALIBRATION_DIR = "calibration"


@dataclass(frozen=True)
class CleanupResult:
    """What one retention pass did."""

    cutoff_ms: int
    deleted_snapshots: int  # files removed for expired events
    cleared_events: int  # events whose snapshot_path was set to NULL
    orphans_removed: int  # unreferenced snapshot files removed
    removed_dirs: int  # empty directories pruned


def cleanup_snapshots(
    config: AppConfig, session: Session, *, now_ms: int | None = None
) -> CleanupResult:
    """Apply the snapshot retention policy once. Idempotent."""
    now = now_ms if now_ms is not None else int(time.time() * 1000)
    cutoff_ms = now - config.snapshot_retention_days * _DAY_MS
    base = Path(config.snapshots_dir)

    deleted = 0
    cleared = 0
    expired = session.scalars(
        select(Event).where(Event.snapshot_path.is_not(None), Event.ts < cutoff_ms)
    ).all()
    for event in expired:
        assert event.snapshot_path is not None
        path = base / event.snapshot_path
        try:
            if path.is_file():
                path.unlink()
                deleted += 1
        except OSError:
            logger.exception("failed to delete expired snapshot %s", path)
            continue  # keep the DB reference; retried on the next pass
        event.snapshot_path = None
        cleared += 1
    session.commit()

    referenced = {
        path
        for path in session.scalars(
            select(Event.snapshot_path).where(Event.snapshot_path.is_not(None))
        )
        if path is not None
    }
    orphans_removed = _prune_orphans(base, referenced, cutoff_ms)
    removed_dirs = _prune_empty_dirs(base)

    result = CleanupResult(
        cutoff_ms=cutoff_ms,
        deleted_snapshots=deleted,
        cleared_events=cleared,
        orphans_removed=orphans_removed,
        removed_dirs=removed_dirs,
    )
    logger.info(
        "snapshot retention (%dd): %d expired file(s) deleted, %d event(s) cleared, "
        "%d orphan(s) removed, %d empty dir(s) pruned",
        config.snapshot_retention_days,
        result.deleted_snapshots,
        result.cleared_events,
        result.orphans_removed,
        result.removed_dirs,
    )
    return result


def _is_calibration(path: Path, base: Path) -> bool:
    parts = path.relative_to(base).parts
    return bool(parts) and parts[0] == _CALIBRATION_DIR


def _prune_orphans(base: Path, referenced: set[str], cutoff_ms: int) -> int:
    """Delete unreferenced snapshot files older than the cutoff (by mtime)."""
    if not base.is_dir():
        return 0
    removed = 0
    for path in base.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in _SNAPSHOT_SUFFIXES:
            continue
        if _is_calibration(path, base):
            continue  # drift references are config, not retained data
        if path.relative_to(base).as_posix() in referenced:
            continue
        try:
            if path.stat().st_mtime * 1000 >= cutoff_ms:
                continue  # young orphan: may belong to an in-flight event
            path.unlink()
            removed += 1
        except OSError:
            logger.exception("failed to delete orphaned snapshot %s", path)
    return removed


def _prune_empty_dirs(base: Path) -> int:
    if not base.is_dir():
        return 0
    removed = 0
    # Deepest first so emptied parents become removable in the same pass.
    for directory in sorted(
        (p for p in base.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True
    ):
        if _is_calibration(directory, base) or directory.name == _CALIBRATION_DIR:
            continue
        try:
            directory.rmdir()  # only succeeds when empty
            removed += 1
        except OSError:
            pass  # not empty — fine
    return removed


class RetentionJob:
    """Daily retention pass on a daemon thread (start/stop with the app)."""

    def __init__(
        self,
        config: AppConfig,
        session_factory: sessionmaker[Session],
        *,
        interval_s: float = 86_400.0,
    ) -> None:
        self._config = config
        self._session_factory = session_factory
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.runs = 0
        self.last_result: CleanupResult | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("RetentionJob already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="retention", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=10)
            self._thread = None

    def run_once(self) -> CleanupResult:
        with self._session_factory() as session:
            result = cleanup_snapshots(self._config, session)
        self.runs += 1
        self.last_result = result
        return result

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self.run_once()
            except Exception:  # noqa: BLE001 - the job must survive bad passes
                logger.exception("retention pass failed; retrying in %.0fs", self._interval_s)
            if self._stop.wait(self._interval_s):
                return
