"""Video input: files, USB devices and RTSP streams via OpenCV.

:class:`VideoSource` is an iterator yielding
``(frame_index, timestamp_monotonic, frame_bgr)`` tuples.

Behaviour by source type:

* ``file`` — ends with normal iterator exhaustion at EOF, or rewinds
  forever when ``loop=True`` (demos). ``paced=True`` replays at the file's
  native FPS via sleep pacing; ``paced=False`` reads as fast as possible.
* ``usb`` / ``rtsp`` — live sources pace themselves. A failed read triggers
  reconnect attempts with exponential backoff (``max_retries``); the outage
  interval is reported through the ``on_gap(from_ts, to_ts, reason)``
  callback (timestamps from ``time.monotonic()``), matching the
  ``data_gaps`` table semantics. If all retries fail, the gap is reported
  with a failure reason and iteration ends.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterator
from typing import Any, Protocol

import cv2
import numpy as np

logger = logging.getLogger(__name__)

SOURCE_TYPES = frozenset({"file", "usb", "rtsp"})
LIVE_SOURCE_TYPES = frozenset({"usb", "rtsp"})

#: Video-page hosts whose URLs must be resolved to a direct stream via yt-dlp
#: (a plain http(s) URL to an .m3u8/.mp4 opens directly in OpenCV/FFmpeg).
WEB_PAGE_STREAM_HOSTS = (
    "youtube.com",
    "youtu.be",
    "twitch.tv",
    "vimeo.com",
    "facebook.com",
    "dailymotion.com",
)


def is_web_page_stream(source: str) -> bool:
    """True for http(s) links to a video *page* (YouTube live etc.)."""
    if not source.startswith(("http://", "https://")):
        return False
    host = source.split("//", 1)[1].split("/", 1)[0].lower()
    return any(host == h or host.endswith("." + h) for h in WEB_PAGE_STREAM_HOSTS)


def resolve_web_stream_url(source: str) -> str:
    """Resolve a video-page URL to its direct media/manifest URL via yt-dlp.

    Called on every (re)open — live manifest URLs expire, so reconnects
    re-resolve. Raises ``SourceOpenError`` with an actionable message when
    yt-dlp is missing or resolution fails.
    """
    try:
        import yt_dlp
    except ImportError as exc:  # pragma: no cover - environment-specific
        raise SourceOpenError(
            "web stream links need the yt-dlp package — install with: pip install yt-dlp"
        ) from exc

    opts = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "format": "best[height<=720]/best",
        "socket_timeout": 15,
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(source, download=False)
    except Exception as exc:
        raise SourceOpenError(f"cannot resolve web stream {source!r}: {exc}") from exc
    if not info:
        raise SourceOpenError(f"cannot resolve web stream {source!r}: no stream info")
    direct = info.get("url") or next(
        (f["url"] for f in reversed(info.get("formats") or []) if f.get("url")), None
    )
    if not direct:
        raise SourceOpenError(f"cannot resolve web stream {source!r}: no playable format")
    return str(direct)


#: Callback signature: ``on_gap(from_ts_monotonic, to_ts_monotonic, reason)``.
GapCallback = Callable[[float, float, str], None]


class CaptureLike(Protocol):
    """The subset of ``cv2.VideoCapture`` that :class:`VideoSource` uses."""

    def isOpened(self) -> bool: ...  # noqa: N802 (OpenCV API name)

    def read(self) -> tuple[bool, Any]: ...

    def release(self) -> None: ...

    def get(self, prop: int) -> float: ...

    def set(self, prop: int, value: float) -> bool: ...


class SourceOpenError(RuntimeError):
    """The video source could not be opened."""


class VideoSource:
    """Iterate frames from a video file, USB camera or RTSP stream.

    Parameters
    ----------
    source:
        File path, USB device index (int or numeric string) or RTSP URL.
    source_type:
        One of ``file`` | ``usb`` | ``rtsp``.
    paced:
        For files only: sleep between frames to replay at native FPS.
    loop:
        For files only: rewind at EOF instead of stopping (demo mode).
    max_retries / backoff_base_s / backoff_max_s:
        Reconnect policy for live sources (exponential backoff).
    on_gap:
        Called with ``(from_ts, to_ts, reason)`` after an outage on a live
        source (both after a successful reconnect and after giving up).
    capture_factory:
        Testing hook: callable returning a ``cv2.VideoCapture``-like object;
        replaces the default OpenCV capture construction.
    """

    def __init__(
        self,
        source: str | int,
        source_type: str = "file",
        *,
        paced: bool = True,
        loop: bool = False,
        max_retries: int = 5,
        backoff_base_s: float = 0.5,
        backoff_max_s: float = 8.0,
        on_gap: GapCallback | None = None,
        capture_factory: Callable[[], CaptureLike] | None = None,
    ) -> None:
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {sorted(SOURCE_TYPES)}: {source_type!r}")
        self._source = source
        self._source_type = source_type
        self._paced = paced
        self._loop = loop
        self._max_retries = max_retries
        self._backoff_base_s = backoff_base_s
        self._backoff_max_s = backoff_max_s
        self._on_gap = on_gap
        self._capture_factory = capture_factory
        self._cap: CaptureLike | None = None
        self._closed = False
        if not self._open():
            raise SourceOpenError(f"cannot open {source_type} source: {source!r}")

    # -- properties ---------------------------------------------------------

    @property
    def source_type(self) -> str:
        return self._source_type

    @property
    def is_live(self) -> bool:
        return self._source_type in LIVE_SOURCE_TYPES

    @property
    def fps(self) -> float:
        """Native FPS as reported by the source (0.0 if unknown)."""
        if self._cap is None:
            return 0.0
        value = float(self._cap.get(cv2.CAP_PROP_FPS))
        return value if value > 0 else 0.0

    @property
    def frame_size(self) -> tuple[int, int]:
        """(width, height) as reported by the source."""
        if self._cap is None:
            return (0, 0)
        width = int(self._cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self._cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        return (width, height)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Release the underlying capture. Idempotent."""
        self._closed = True
        self._release()

    def __enter__(self) -> VideoSource:
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- iteration ----------------------------------------------------------

    def __iter__(self) -> Iterator[tuple[int, float, np.ndarray]]:
        period = 0.0
        if self._paced and not self.is_live:
            fps = self.fps
            period = 1.0 / fps if fps > 0 else 0.0
        t0: float | None = None
        frame_index = 0
        while True:
            frame = self._next_frame()
            if frame is None:
                return
            if period > 0.0:
                if t0 is None:
                    t0 = time.monotonic()
                else:
                    delay = t0 + frame_index * period - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)
            yield frame_index, time.monotonic(), frame
            frame_index += 1

    # -- internals ----------------------------------------------------------

    def _make_capture(self) -> CaptureLike:
        if self._capture_factory is not None:
            return self._capture_factory()
        if self._source_type == "usb":
            return cv2.VideoCapture(int(self._source))
        if self._source_type == "rtsp":
            source = str(self._source)
            if is_web_page_stream(source):
                # Re-resolved on every open: live manifests expire, and the
                # reconnect path lands here again with a fresh resolution.
                try:
                    source = resolve_web_stream_url(source)
                except SourceOpenError as exc:
                    logger.warning("%s", exc)
                    return cv2.VideoCapture("")  # unopened -> normal retry path
            return cv2.VideoCapture(source, cv2.CAP_FFMPEG)
        return cv2.VideoCapture(str(self._source))

    def _open(self) -> bool:
        cap = self._make_capture()
        if not cap.isOpened():
            cap.release()
            return False
        self._cap = cap
        return True

    def _release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def _next_frame(self) -> np.ndarray | None:
        if self._closed or self._cap is None:
            return None
        ok, frame = self._cap.read()
        if ok:
            return frame
        if self.is_live:
            return self._reconnect_and_read()
        if self._loop:
            return self._rewind_and_read()
        return None

    def _rewind_and_read(self) -> np.ndarray | None:
        assert self._cap is not None
        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        ok, frame = self._cap.read()
        if ok:
            return frame
        # Some backends cannot seek: fall back to a full reopen.
        self._release()
        if self._open():
            assert self._cap is not None
            ok, frame = self._cap.read()
            if ok:
                return frame
        return None

    def _reconnect_and_read(self) -> np.ndarray | None:
        gap_start = time.monotonic()
        self._release()
        for attempt in range(1, self._max_retries + 1):
            delay = min(self._backoff_base_s * 2 ** (attempt - 1), self._backoff_max_s)
            logger.warning(
                "source %r lost; reconnect attempt %d/%d in %.2fs",
                self._source,
                attempt,
                self._max_retries,
                delay,
            )
            time.sleep(delay)
            if self._closed:
                break
            if not self._open():
                continue
            assert self._cap is not None
            ok, frame = self._cap.read()
            if ok:
                self._emit_gap(gap_start, f"reconnected after {attempt} attempt(s)")
                return frame
            self._release()
        self._emit_gap(gap_start, f"reconnect failed after {self._max_retries} attempt(s)")
        return None

    def _emit_gap(self, gap_start: float, reason: str) -> None:
        if self._on_gap is not None:
            self._on_gap(gap_start, time.monotonic(), reason)
