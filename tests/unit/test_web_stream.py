"""Web-page stream links (YouTube live etc.) as camera sources."""

from __future__ import annotations

import sys
import types

import pytest

from toskana.vision.capture import (
    SourceOpenError,
    is_web_page_stream,
    resolve_web_stream_url,
)


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://www.youtube.com/watch?v=abc", True),
        ("https://youtu.be/abc", True),
        ("http://twitch.tv/somecam", True),
        ("https://sub.youtube.com/live/x", True),
        ("https://notyoutube.com/watch", False),
        ("https://example.com/stream.m3u8", False),  # direct URL: no resolution
        ("rtsp://user:pw@10.0.0.5:554/stream1", False),
        ("0", False),
        ("./video.mp4", False),
    ],
)
def test_is_web_page_stream(source: str, expected: bool) -> None:
    assert is_web_page_stream(source) is expected


class _FakeYdl:
    def __init__(self, info: object) -> None:
        self._info = info

    def __call__(self, opts: dict) -> _FakeYdl:
        return self

    def __enter__(self) -> _FakeYdl:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, url: str, download: bool) -> object:
        assert download is False
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _install_fake_yt_dlp(monkeypatch: pytest.MonkeyPatch, info: object) -> None:
    module = types.ModuleType("yt_dlp")
    module.YoutubeDL = _FakeYdl(info)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "yt_dlp", module)


def test_resolve_returns_direct_url(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yt_dlp(monkeypatch, {"url": "https://cdn.example/manifest.m3u8"})
    url = resolve_web_stream_url("https://www.youtube.com/watch?v=abc")
    assert url == "https://cdn.example/manifest.m3u8"


def test_resolve_falls_back_to_formats(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yt_dlp(
        monkeypatch,
        {
            "formats": [
                {"url": "https://cdn.example/low.mp4"},
                {"url": "https://cdn.example/best.mp4"},
            ]
        },
    )
    assert resolve_web_stream_url("https://youtu.be/abc") == "https://cdn.example/best.mp4"


def test_resolve_failure_raises_source_open_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yt_dlp(monkeypatch, RuntimeError("geo blocked"))
    with pytest.raises(SourceOpenError, match="geo blocked"):
        resolve_web_stream_url("https://www.youtube.com/watch?v=abc")


def test_resolve_no_playable_format(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_yt_dlp(monkeypatch, {"formats": [{"height": 720}]})
    with pytest.raises(SourceOpenError, match="no playable format"):
        resolve_web_stream_url("https://www.youtube.com/watch?v=abc")
