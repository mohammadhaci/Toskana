"""Camera connection wizard: vendor presets + a "test connection" probe.

``POST /restaurants/{rid}/cameras/test-source`` opens the source once
(``VideoSource`` with ``max_retries=0``), grabs a first frame and returns a
downscaled JPEG preview. Opening an RTSP URL can block inside OpenCV/FFmpeg
well beyond any polite UI wait, so the probe runs in a worker thread that
is given an overall time budget (:data:`PROBE_TIMEOUT_S`); on timeout the
request returns ``ok=false`` immediately while the abandoned thread lingers
until cv2 itself gives up (its capture is closed by the thread on exit).

Probe failures are results, not HTTP errors: the endpoint always answers
200 with ``ok`` true/false. The password never appears in a response or a
log line — echoed URLs are masked via :func:`mask_url_password`.
"""

from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from pathlib import Path
from typing import NamedTuple

import cv2
from fastapi import APIRouter

from toskana.api import schemas
from toskana.api.deps import SessionDep, restaurant_or_404
from toskana.camera_presets import PRESETS, build_rtsp_url, mask_url_password
from toskana.vision.capture import SourceOpenError, VideoSource

router = APIRouter(tags=["cameras"])

#: Overall probe budget in seconds (open + first frame). Patched in tests.
PROBE_TIMEOUT_S = 8.0

#: Preview JPEGs are downscaled to at most this width.
SNAPSHOT_MAX_WIDTH = 480

_FRIENDLY_OPEN_ERROR = {
    "rtsp": (
        "Could not connect to the camera stream. Check the IP address, port, "
        "username/password, and that RTSP is enabled on the camera "
        "(see docs/cameras.md)."
    ),
    "usb": (
        "No webcam found at this device index. Check that the camera is "
        "plugged into the Toskana machine and try index 0, 1 or 2."
    ),
    "file": "The video file could not be opened. Check the path and file format.",
}

_TIMEOUT_ERROR = (
    "Connection test timed out. The address may be unreachable — check the "
    "IP address and that the camera is on the same network (see docs/cameras.md)."
)

_NO_FRAME_ERROR = (
    "The source opened but delivered no image. The stream may be paused or "
    "the channel number may be wrong."
)


@router.get("/camera-presets", response_model=list[schemas.CameraPresetRead])
def list_camera_presets() -> list[schemas.CameraPresetRead]:
    return [
        schemas.CameraPresetRead(
            key=preset.key,
            label=preset.label,
            default_port=preset.default_port,
            needs_channel=preset.needs_channel,
            url_template=preset.url_template,
        )
        for preset in PRESETS
    ]


class ProbeResult(NamedTuple):
    width: int
    height: int
    fps: float
    snapshot_b64: str


def _probe_source(source: str | int, source_type: str) -> ProbeResult:
    """Open the source once and grab a first frame (runs in a worker thread)."""
    with VideoSource(source, source_type, paced=False, max_retries=0) as video:
        width, height = video.frame_size
        fps = video.fps
        for _, _, frame in video:
            if frame.shape[1] > SNAPSHOT_MAX_WIDTH:
                scale = SNAPSHOT_MAX_WIDTH / frame.shape[1]
                frame = cv2.resize(
                    frame, (SNAPSHOT_MAX_WIDTH, max(1, round(frame.shape[0] * scale)))
                )
            ok, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if not ok:
                raise SourceOpenError("could not encode the preview image")
            return ProbeResult(
                width=width or int(frame.shape[1]),
                height=height or int(frame.shape[0]),
                fps=fps,
                snapshot_b64=base64.b64encode(buffer.tobytes()).decode("ascii"),
            )
    raise SourceOpenError(_NO_FRAME_ERROR)


@router.post(
    "/restaurants/{restaurant_id}/cameras/test-source",
    response_model=schemas.CameraTestResult,
)
def test_camera_source(
    restaurant_id: int, body: schemas.CameraTestSource, session: SessionDep
) -> schemas.CameraTestResult:
    restaurant_or_404(session, restaurant_id)

    if body.preset_key is not None:
        source_type = "rtsp"
        try:
            source_url = build_rtsp_url(
                body.preset_key,
                ip=body.ip or "",
                user=body.username,
                password=body.password,
                port=body.port,
                channel=body.channel,
            )
        except ValueError as exc:
            return schemas.CameraTestResult(ok=False, source_type=source_type, error=str(exc))
    else:
        source_type = str(body.source_type)
        source_url = str(body.source_url).strip()

    masked = mask_url_password(source_url)
    source: str | int = int(source_url) if source_type == "usb" else source_url

    if source_type == "file" and not Path(source_url).is_file():
        return schemas.CameraTestResult(
            ok=False,
            source_type=source_type,
            source_url_masked=masked,
            error=f"No video file found at {source_url!r}. Check the path.",
        )

    # One throwaway worker per probe: a blocked cv2 open must not stall the
    # next probe. shutdown(wait=False) leaves the thread to finish on its own.
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="camera-probe")
    try:
        future = executor.submit(_probe_source, source, source_type)
        try:
            result = future.result(timeout=PROBE_TIMEOUT_S)
        except FutureTimeoutError:
            return schemas.CameraTestResult(
                ok=False, source_type=source_type, source_url_masked=masked, error=_TIMEOUT_ERROR
            )
        except SourceOpenError as exc:
            error = str(exc)
            if error.startswith("cannot open"):  # VideoSource's message echoes the URL
                error = _FRIENDLY_OPEN_ERROR[source_type]
            return schemas.CameraTestResult(
                ok=False, source_type=source_type, source_url_masked=masked, error=error
            )
    finally:
        executor.shutdown(wait=False)

    return schemas.CameraTestResult(
        ok=True,
        source_type=source_type,
        source_url_masked=masked,
        source_url=source_url,
        width=result.width,
        height=result.height,
        fps=result.fps,
        snapshot_b64=result.snapshot_b64,
    )
