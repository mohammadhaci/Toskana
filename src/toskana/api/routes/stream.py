"""Live camera preview: MJPEG stream + single-JPEG snapshot.

Frames come from the :class:`~toskana.vision.manager.PipelineManager`'s
latest annotated frame buffer (boxes + tracks + line + live counts drawn by
the pipeline thread); the stream re-sends the newest frame at most ~10 fps.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse

from toskana.api.deps import ManagerDep, SessionDep, camera_or_404
from toskana.vision.manager import PipelineManager

router = APIRouter(tags=["stream"])

_BOUNDARY = "toskana-frame"
_FRAME_INTERVAL_S = 0.1  # ~10 fps cap
_FIRST_FRAME_TIMEOUT_S = 10.0


async def _wait_first_frame(
    manager: PipelineManager, camera_id: int, timeout_s: float
) -> tuple[bytes, int] | None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        latest = manager.latest_jpeg(camera_id)
        if latest is not None:
            return latest
        if not manager.is_running(camera_id):
            return None
        await asyncio.sleep(0.05)
    return None


def _part(jpeg: bytes) -> bytes:
    return (
        (
            f"--{_BOUNDARY}\r\nContent-Type: image/jpeg\r\nContent-Length: {len(jpeg)}\r\n\r\n"
        ).encode()
        + jpeg
        + b"\r\n"
    )


@router.get("/stream/{camera_id}")
async def mjpeg_stream(
    camera_id: int,
    request: Request,
    session: SessionDep,
    manager: ManagerDep,
    max_frames: int | None = Query(
        None, ge=1, description="End the stream after this many frames (unbounded if omitted)"
    ),
) -> StreamingResponse:
    camera_or_404(session, camera_id)
    if not manager.is_running(camera_id):
        raise HTTPException(status_code=409, detail="camera pipeline is not running")
    first = await _wait_first_frame(manager, camera_id, _FIRST_FRAME_TIMEOUT_S)
    if first is None:
        raise HTTPException(status_code=409, detail="no frames available yet")

    async def frames() -> AsyncIterator[bytes]:
        sent = 1
        yield _part(first[0])
        while manager.is_running(camera_id) and (max_frames is None or sent < max_frames):
            if await request.is_disconnected():
                return
            await asyncio.sleep(_FRAME_INTERVAL_S)
            latest = manager.latest_jpeg(camera_id)
            if latest is not None:
                sent += 1
                yield _part(latest[0])

    return StreamingResponse(
        frames(), media_type=f"multipart/x-mixed-replace; boundary={_BOUNDARY}"
    )


@router.get("/snapshot/{camera_id}")
async def snapshot(camera_id: int, session: SessionDep, manager: ManagerDep) -> Response:
    camera_or_404(session, camera_id)
    if not manager.is_running(camera_id) and manager.latest_jpeg(camera_id) is None:
        raise HTTPException(status_code=409, detail="camera pipeline is not running")
    latest = await _wait_first_frame(manager, camera_id, _FIRST_FRAME_TIMEOUT_S)
    if latest is None:
        latest = manager.latest_jpeg(camera_id)  # last frame of a stopped pipeline
        if latest is None:
            raise HTTPException(status_code=409, detail="no frames available yet")
    return Response(content=latest[0], media_type="image/jpeg")
