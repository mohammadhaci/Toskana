"""One-off video analysis jobs: count crossings in an uploaded clip or URL.

:class:`AnalysisJobManager` runs at most **one** :class:`CameraPipeline` at a
time (a simple worker thread + FIFO queue protects the CPU), unpaced and
without looping, over a video file that was either uploaded through the
dashboard or downloaded from a URL via ``yt-dlp``.

Events flow through the normal ``bus -> EventWriter`` path: each analyzed
file gets one reusable ``Camera`` row (``source_type='file'``,
``enabled=False`` so the :class:`~toskana.vision.manager.PipelineManager`
never auto-starts it) plus a ``Line`` row built from the request, so the
results show up on the Events page and in the stats — snapshots included.
Event timestamps advance with *media time* from the moment the job starts
(see :mod:`toskana.vision.pipeline`).

Job state machine::

    queued -> downloading (URL jobs only) -> running -> done
                                                     -> error
    any non-terminal state -> cancelled (best-effort via /cancel)

Jobs are kept **in memory only** (newest first); they do not survive a
server restart. The counted events do — they are persisted like any other
crossing.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.models import Camera, Line, Restaurant
from toskana.events.bus import EventBus
from toskana.events.writer import new_event_id
from toskana.vision.line_crossing import LineSpec, parse_count_directions
from toskana.vision.manager import PipelineManager
from toskana.vision.pipeline import DIRECTION_TO_DB, CameraPipeline, PipelineSpec
from toskana.vision.presets import resolve_preset

logger = logging.getLogger(__name__)

#: Terminal job states (no further transitions).
TERMINAL_STATES = frozenset({"done", "error", "cancelled"})

_YT_DLP_HINT = (
    "Check that the link is a valid, public video URL. If the problem "
    "persists, update the downloader: pip install -U yt-dlp"
)


def new_job_id() -> str:
    """A fresh ULID string (also creation-ordered, like event ids)."""
    return new_event_id()


@dataclass
class AnalysisLineSpec:
    """Normalized counting line requested for one analysis job."""

    x1: float = 0.5
    y1: float = 0.0
    x2: float = 0.5
    y2: float = 1.0
    count_directions: str = "out,in"


@dataclass
class AnalysisJob:
    """Mutable book-keeping for one job (guarded by the manager's lock)."""

    id: str
    restaurant_id: int
    video_name: str
    backend: str  # yolo | synthetic
    line: AnalysisLineSpec
    source_url: str | None = None  # URL jobs only
    video_path: str | None = None  # set after download for URL jobs
    status: str = "queued"  # queued|downloading|running|done|error|cancelled
    error: str | None = None
    camera_id: int | None = None
    line_id: int | None = None
    frames_done: int = 0
    frames_total: int | None = None
    #: counts[direction][class_name] with direction in {out, in}.
    counts: dict[str, dict[str, int]] = field(default_factory=lambda: {"out": {}, "in": {}})
    created_ts: int = field(default_factory=lambda: int(time.time() * 1000))
    finished_ts: int | None = None
    cancel_requested: bool = False
    pipeline: CameraPipeline | None = None  # while running (for cancel)


class AnalysisJobManager:
    """Run one-off counting jobs over video files, one at a time."""

    def __init__(
        self,
        config: AppConfig,
        *,
        bus: EventBus,
        session_factory: sessionmaker[Session],
    ) -> None:
        self._config = config
        self._bus = bus
        self._session_factory = session_factory
        self._lock = threading.Lock()
        self._jobs: list[AnalysisJob] = []  # newest first
        self._queue: queue.Queue[Any] = queue.Queue()
        self._worker: threading.Thread | None = None
        self._stopping = False

    # -- public API ------------------------------------------------------------

    def submit(
        self,
        restaurant_id: int,
        *,
        video_path: str | None = None,
        url: str | None = None,
        video_name: str,
        backend: str = "yolo",
        line: AnalysisLineSpec | None = None,
    ) -> dict[str, Any]:
        """Queue a new job (exactly one of ``video_path``/``url``)."""
        if (video_path is None) == (url is None):
            raise ValueError("provide exactly one of video_path or url")
        if backend not in ("yolo", "synthetic"):
            raise ValueError(f"unknown backend: {backend!r}")
        job = AnalysisJob(
            id=new_job_id(),
            restaurant_id=restaurant_id,
            video_name=video_name,
            backend=backend,
            line=line if line is not None else AnalysisLineSpec(),
            source_url=url,
            video_path=video_path,
        )
        with self._lock:
            if self._stopping:
                raise RuntimeError("AnalysisJobManager is shutting down")
            self._jobs.insert(0, job)
            self._ensure_worker()
        self._queue.put(job)
        return self._job_dict(job)

    def list_jobs(self, restaurant_id: int) -> list[dict[str, Any]]:
        """All jobs of one restaurant, newest first."""
        with self._lock:
            return [self._job_dict(job) for job in self._jobs if job.restaurant_id == restaurant_id]

    def get_job(self, restaurant_id: int, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._find(restaurant_id, job_id)
            return None if job is None else self._job_dict(job)

    def cancel(self, restaurant_id: int, job_id: str) -> dict[str, Any] | None:
        """Best-effort stop; terminal jobs are a no-op. None = unknown job."""
        with self._lock:
            job = self._find(restaurant_id, job_id)
            if job is None:
                return None
            if job.status not in TERMINAL_STATES:
                job.cancel_requested = True
                if job.status == "queued":
                    self._finish(job, "cancelled")
                elif job.pipeline is not None:
                    job.pipeline.stop()
            return self._job_dict(job)

    def stop(self, *, join_timeout_s: float = 10.0) -> None:
        """Shutdown: cancel the running job and join the worker thread."""
        with self._lock:
            self._stopping = True
            worker = self._worker
            for job in self._jobs:
                if job.status not in TERMINAL_STATES:
                    job.cancel_requested = True
                    if job.pipeline is not None:
                        job.pipeline.stop()
        if worker is not None:
            self._queue.put(None)  # wake the worker so it can exit
            worker.join(join_timeout_s)

    # -- internals ---------------------------------------------------------------

    def _find(self, restaurant_id: int, job_id: str) -> AnalysisJob | None:
        for job in self._jobs:
            if job.id == job_id and job.restaurant_id == restaurant_id:
                return job
        return None

    def _ensure_worker(self) -> None:
        """Start the single worker thread lazily (caller holds the lock)."""
        if self._worker is None or not self._worker.is_alive():
            self._worker = threading.Thread(target=self._work, name="analysis-worker", daemon=True)
            self._worker.start()

    def _work(self) -> None:
        while True:
            job = self._queue.get()
            if job is None:  # shutdown sentinel
                return
            with self._lock:
                if self._stopping:
                    return
                if job.status != "queued":  # cancelled while waiting
                    continue
            try:
                self._run_job(job)
            except Exception as exc:  # noqa: BLE001 - job errors must stay observable
                logger.exception("analysis job %s failed", job.id)
                with self._lock:
                    job.error = f"{type(exc).__name__}: {exc}"
                    self._finish(job, "error")

    def _run_job(self, job: AnalysisJob) -> None:
        # 1. URL jobs: download first.
        if job.video_path is None:
            with self._lock:
                job.status = "downloading"
            try:
                assert job.source_url is not None
                path, title = self._download(job.source_url)
            except Exception as exc:  # yt-dlp raises many error types
                with self._lock:
                    job.error = f"download failed: {type(exc).__name__}: {exc}. {_YT_DLP_HINT}"
                    self._finish(job, "error")
                return
            with self._lock:
                job.video_path = path
                if title:
                    job.video_name = title
                if job.cancel_requested:
                    self._finish(job, "cancelled")
                    return

        if not Path(job.video_path or "").is_file():
            with self._lock:
                job.error = f"video file not found: {job.video_path}"
                self._finish(job, "error")
            return

        # 2. Camera + line rows so events land in the normal tables.
        with self._session_factory() as session:
            camera_id, line_id, spec = self._prepare(session, job)
        frames_total = _probe_frame_count(str(job.video_path))
        with self._lock:
            job.camera_id = camera_id
            job.line_id = line_id
            job.frames_total = frames_total
            if job.cancel_requested:
                self._finish(job, "cancelled")
                return
            pipeline = CameraPipeline(spec, bus=self._bus, on_frame=self._hook(job))
            job.pipeline = pipeline
            job.status = "running"

        # 3. Run to exhaustion (unpaced, no loop). Backend construction
        # happens inside run_once — a missing ultralytics install or bad
        # weights surface here as an error state, never a crash.
        try:
            result = pipeline.run_once()
        except Exception as exc:  # noqa: BLE001 - keep the app alive, report the job
            message = f"{type(exc).__name__}: {exc}"
            if job.backend == "yolo":
                message += (
                    " — the YOLO backend needs the ML extra and model weights: "
                    "pip install 'toskana[ml]'"
                )
            with self._lock:
                job.error = message
                self._finish(job, "error")
            return

        with self._lock:
            job.counts = _db_counts(result.counts)
            self._finish(job, "cancelled" if job.cancel_requested else "done")

    def _finish(self, job: AnalysisJob, status: str) -> None:
        """Terminal transition (caller holds the lock)."""
        job.status = status
        job.finished_ts = int(time.time() * 1000)
        job.pipeline = None

    def _hook(self, job: AnalysisJob) -> Any:
        def on_frame(frame_index: int, ts_ms: int, frame: Any, tracked: list[Any]) -> None:
            pipeline = job.pipeline
            with self._lock:
                job.frames_done = frame_index + 1
                if pipeline is not None:
                    job.counts = _db_counts(pipeline.result.counts)

        return on_frame

    # -- download (yt-dlp) ----------------------------------------------------------

    def _download(self, url: str) -> tuple[str, str | None]:
        """Fetch ``url`` into the uploads dir; returns (path, title)."""
        import yt_dlp  # deferred: optional dependency (train extra)

        dest_dir = Path(self._config.uploads_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        opts = {
            # Single pre-merged file (no ffmpeg merge needed), capped at 720p.
            "format": "best[height<=720][ext=mp4]/best[height<=720]/best",
            "outtmpl": str(dest_dir / "%(id)s.%(ext)s"),
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "retries": 0,
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            probe = ydl.extract_info(url, download=False)
            assert probe is not None
            if probe.get("is_live"):
                raise ValueError(
                    "live streams cannot be analyzed — use a recorded video "
                    "(or connect the stream as an RTSP camera instead)"
                )
            duration = probe.get("duration")
            if duration and duration > self._config.max_url_video_seconds:
                raise ValueError(
                    f"video is {int(duration) // 60} min long — the limit for URL "
                    f"analysis is {self._config.max_url_video_seconds // 60} min; "
                    "download a shorter clip or upload a trimmed file"
                )
            info = ydl.extract_info(url, download=True)
            assert info is not None
            filename = ydl.prepare_filename(info)
        title = info.get("title")
        return filename, str(title) if title else None

    # -- spec building ----------------------------------------------------------------

    def _prepare(self, session: Session, job: AnalysisJob) -> tuple[int, int, PipelineSpec]:
        """Find-or-create the per-file camera + line; build the pipeline spec."""
        restaurant = session.get(Restaurant, job.restaurant_id)
        if restaurant is None:
            raise ValueError(f"restaurant {job.restaurant_id} not found")

        source_url = str(job.video_path)
        camera = session.scalar(
            select(Camera).where(
                Camera.restaurant_id == job.restaurant_id,
                Camera.source_type == "file",
                Camera.source_url == source_url,
                Camera.enabled.is_(False),
            )
        )
        if camera is None:
            camera = Camera(
                restaurant_id=job.restaurant_id,
                name=f"Analyse: {job.video_name}"[:200],
                source_type="file",
                source_url=source_url,
                enabled=False,  # PipelineManager.start_all() must skip it
            )
            session.add(camera)
            session.flush()

        requested = job.line
        line = session.scalar(
            select(Line).where(
                Line.camera_id == camera.id,
                Line.x1 == requested.x1,
                Line.y1 == requested.y1,
                Line.x2 == requested.x2,
                Line.y2 == requested.y2,
                Line.count_directions == requested.count_directions,
            )
        )
        if line is None:
            line = Line(
                restaurant_id=job.restaurant_id,
                camera_id=camera.id,
                name="Analysis line",
                x1=requested.x1,
                y1=requested.y1,
                x2=requested.x2,
                y2=requested.y2,
                count_directions=requested.count_directions,
            )
            session.add(line)
            session.flush()
        session.commit()

        line_spec = LineSpec(
            x1=line.x1,
            y1=line.y1,
            x2=line.x2,
            y2=line.y2,
            line_id=line.id,
            name=line.name,
            count_directions=parse_count_directions(line.count_directions),
            min_track_age=line.min_track_age,
            hysteresis_px=line.hysteresis_px,
            cooldown_ms=line.cooldown_ms,
        )

        # Reuse the manager's mapping/model resolution so analysis events get
        # the same category / menu-item attribution as live counting.
        if job.backend == "synthetic":
            rules = PipelineManager._synthetic_rules(session, job.restaurant_id)
            model_path = "yolov8n.pt"  # unused by the synthetic backend
        else:
            model = PipelineManager._effective_model(session, camera)
            rules = PipelineManager._model_rules(session, job.restaurant_id, model)
            model_path = model.path if model is not None else "yolov8n.pt"
        menu_items = PipelineManager._menu_item_infos(session, job.restaurant_id)

        preset = resolve_preset(self._config.performance_preset)
        spec = PipelineSpec(
            restaurant_id=job.restaurant_id,
            camera_id=camera.id,
            source=source_url,
            restaurant_slug=restaurant.slug,
            source_type="file",
            backend=job.backend,
            model_path=model_path,
            device=self._config.device,
            paced=False,  # analysis runs as fast as the hardware allows
            loop=False,  # exactly one pass over the clip
            lines=(line_spec,),
            mapping_rules=rules,
            menu_items=menu_items,
            snapshots_dir=self._config.snapshots_dir,
            frame_skip=preset.frame_skip if job.backend == "yolo" else 1,
            imgsz=preset.imgsz,
        )
        return camera.id, line.id, spec

    # -- serialization -------------------------------------------------------------------

    @staticmethod
    def _job_dict(job: AnalysisJob) -> dict[str, Any]:
        counts = {direction: dict(per) for direction, per in job.counts.items()}
        return {
            "id": job.id,
            "restaurant_id": job.restaurant_id,
            "status": job.status,
            "video_name": job.video_name,
            "source_url": job.source_url,
            "backend": job.backend,
            "camera_id": job.camera_id,
            "line_id": job.line_id,
            "frames_done": job.frames_done,
            "frames_total": job.frames_total,
            "counts": counts,
            "total_out": sum(counts.get("out", {}).values()),
            "total_in": sum(counts.get("in", {}).values()),
            "error": job.error,
            "created_ts": job.created_ts,
            "finished_ts": job.finished_ts,
        }


def _db_counts(counts: dict[str, dict[str, int]]) -> dict[str, dict[str, int]]:
    """Pipeline counts (positive/negative) -> API counts (out/in)."""
    return {
        DIRECTION_TO_DB[direction]: dict(per_class)
        for direction, per_class in counts.items()
        if direction in DIRECTION_TO_DB
    }


def _probe_frame_count(path: str) -> int | None:
    """Total frame count of a video file (None when the codec won't say)."""
    import cv2

    cap = cv2.VideoCapture(path)
    try:
        if not cap.isOpened():
            return None
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        return total if total > 0 else None
    finally:
        cap.release()
