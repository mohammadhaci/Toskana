"""Video analysis jobs: upload a clip (or paste a URL) and count crossings.

``POST /restaurants/{id}/analysis`` accepts multipart form data with either
a ``file`` (video upload, streamed to ``{uploads_dir}/{ulid}-{name}``, size
capped by ``max_upload_bytes``) **or** a ``url`` form field (http(s),
downloaded via ``yt-dlp``). Optional fields: a normalized counting line
(``x1``/``y1``/``x2``/``y2``, default vertical at x=0.5), ``backend``
(``yolo`` default, ``synthetic`` for the demo clips) and
``count_directions``. Jobs run one at a time in the background
(:class:`~toskana.analysis.AnalysisJobManager`); the resulting events land
in the normal events tables under a dedicated disabled camera per file.

Job listings live in memory only — a server restart clears the list, the
counted events stay.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Form, HTTPException, UploadFile

from toskana.analysis import AnalysisLineSpec, new_job_id
from toskana.api import schemas
from toskana.api.deps import AnalysisDep, ConfigDep, SessionDep, restaurant_or_404
from toskana.vision.line_crossing import parse_count_directions

router = APIRouter(tags=["analysis"])

_NESTED = "/restaurants/{restaurant_id}/analysis"

_COPY_CHUNK = 1024 * 1024  # 1 MiB

_YT_DLP_MISSING = (
    "URL analysis needs yt-dlp, which is not installed on this server. "
    "Install it with: pip install yt-dlp — or upload the video file directly."
)


def _yt_dlp_available() -> bool:
    """Is the optional downloader importable? (patchable in tests)"""
    return importlib.util.find_spec("yt_dlp") is not None


def _safe_filename(name: str | None) -> str:
    """Basename with anything but [A-Za-z0-9._-] replaced (never empty)."""
    base = Path(name or "video").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._") or "video"
    return cleaned[:120]


def _line_from_form(
    x1: float | None,
    y1: float | None,
    x2: float | None,
    y2: float | None,
    count_directions: str,
) -> AnalysisLineSpec:
    coords = (x1, y1, x2, y2)
    given = [value for value in coords if value is not None]
    if given and len(given) != 4:
        raise HTTPException(
            status_code=422, detail="line needs all four coordinates (x1, y1, x2, y2)"
        )
    try:
        parse_count_directions(count_directions)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not given:
        return AnalysisLineSpec(count_directions=count_directions)
    assert x1 is not None and y1 is not None and x2 is not None and y2 is not None
    if (x1, y1) == (x2, y2):
        raise HTTPException(status_code=422, detail="line endpoints must differ")
    return AnalysisLineSpec(x1=x1, y1=y1, x2=x2, y2=y2, count_directions=count_directions)


def _save_upload(file: UploadFile, config: ConfigDep) -> Path:
    """Stream the upload to ``{uploads_dir}/{ulid}-{safe_name}`` (size-capped)."""
    uploads_dir = Path(config.uploads_dir)
    uploads_dir.mkdir(parents=True, exist_ok=True)
    dest = uploads_dir / f"{new_job_id()}-{_safe_filename(file.filename)}"
    written = 0
    try:
        with dest.open("wb") as out:
            while chunk := file.file.read(_COPY_CHUNK):
                written += len(chunk)
                if written > config.max_upload_bytes:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"video exceeds the upload limit of {config.max_upload_bytes} bytes"
                        ),
                    )
                out.write(chunk)
    except HTTPException:
        dest.unlink(missing_ok=True)
        raise
    if written == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=422, detail="uploaded video file is empty")
    return dest


NormForm = Annotated[float | None, Form(ge=0.0, le=1.0)]


@router.post(_NESTED, response_model=schemas.AnalysisJobRead, status_code=201)
def create_analysis_job(
    restaurant_id: int,
    session: SessionDep,
    config: ConfigDep,
    analysis: AnalysisDep,
    file: UploadFile | None = None,
    url: Annotated[str | None, Form()] = None,
    x1: NormForm = None,
    y1: NormForm = None,
    x2: NormForm = None,
    y2: NormForm = None,
    backend: Annotated[str, Form()] = "yolo",
    count_directions: Annotated[str, Form()] = "out,in",
) -> schemas.AnalysisJobRead:
    restaurant_or_404(session, restaurant_id)
    if (file is None) == (url is None):
        raise HTTPException(
            status_code=422, detail="provide either a video file or a url (not both)"
        )
    if backend not in ("yolo", "synthetic"):
        raise HTTPException(
            status_code=422, detail=f"backend must be 'yolo' or 'synthetic': {backend!r}"
        )
    line = _line_from_form(x1, y1, x2, y2, count_directions)

    if url is not None:
        url = url.strip()
        if not url.lower().startswith(("http://", "https://")):
            raise HTTPException(status_code=422, detail="url must start with http:// or https://")
        if not _yt_dlp_available():
            raise HTTPException(status_code=422, detail=_YT_DLP_MISSING)
        job = analysis.submit(restaurant_id, url=url, video_name=url, backend=backend, line=line)
    else:
        assert file is not None
        dest = _save_upload(file, config)
        job = analysis.submit(
            restaurant_id,
            video_path=str(dest),
            video_name=_safe_filename(file.filename),
            backend=backend,
            line=line,
        )
    return schemas.AnalysisJobRead(**job)


@router.get(_NESTED, response_model=list[schemas.AnalysisJobRead])
def list_analysis_jobs(
    restaurant_id: int, session: SessionDep, analysis: AnalysisDep
) -> list[schemas.AnalysisJobRead]:
    """This restaurant's jobs, newest first (in-memory, cleared on restart)."""
    restaurant_or_404(session, restaurant_id)
    return [schemas.AnalysisJobRead(**job) for job in analysis.list_jobs(restaurant_id)]


@router.get(_NESTED + "/{job_id}", response_model=schemas.AnalysisJobRead)
def get_analysis_job(
    restaurant_id: int, job_id: str, session: SessionDep, analysis: AnalysisDep
) -> schemas.AnalysisJobRead:
    restaurant_or_404(session, restaurant_id)
    job = analysis.get_job(restaurant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    return schemas.AnalysisJobRead(**job)


@router.post(_NESTED + "/{job_id}/cancel", response_model=schemas.AnalysisJobRead)
def cancel_analysis_job(
    restaurant_id: int, job_id: str, session: SessionDep, analysis: AnalysisDep
) -> schemas.AnalysisJobRead:
    """Best-effort stop; already-finished jobs are returned unchanged."""
    restaurant_or_404(session, restaurant_id)
    job = analysis.cancel(restaurant_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="analysis job not found")
    return schemas.AnalysisJobRead(**job)
