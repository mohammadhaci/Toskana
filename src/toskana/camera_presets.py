"""Vendor RTSP URL presets for the camera connection wizard.

Single source of truth for the URL patterns documented in
``docs/cameras.md`` — keep both in sync. Templates use the placeholders
``{user}`` ``{password}`` ``{ip}`` ``{port}`` ``{channel}``; credentials are
URL-encoded by :func:`build_rtsp_url` so passwords with ``@``, ``:`` or
``/`` work verbatim (``@`` -> ``%40``). Channels are 1-based; Hikvision
NVR channel N maps to stream id ``N01`` (main) / ``N02`` (sub), Reolink
zero-pads (``h264Preview_01_main``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote

DEFAULT_RTSP_PORT = 554

#: Rendered in place of the real password when a URL is echoed back.
PASSWORD_MASK = "•••"


@dataclass(frozen=True)
class CameraPreset:
    """One vendor entry of the wizard's dropdown."""

    key: str
    label: str
    #: ``None`` for the generic preset (the user pastes a full RTSP URL).
    url_template: str | None
    notes_key: str
    default_port: int = DEFAULT_RTSP_PORT

    @property
    def needs_channel(self) -> bool:
        """Whether the URL pattern addresses an NVR/multi-channel stream."""
        return self.url_template is not None and "{channel" in self.url_template


PRESETS: tuple[CameraPreset, ...] = (
    CameraPreset(
        key="hikvision",
        label="Hikvision (main stream)",
        url_template="rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}01",
        notes_key="hikvision",
    ),
    CameraPreset(
        key="hikvision_sub",
        label="Hikvision (substream)",
        url_template="rtsp://{user}:{password}@{ip}:{port}/Streaming/Channels/{channel}02",
        notes_key="hikvision",
    ),
    CameraPreset(
        key="dahua",
        label="Dahua / Amcrest / Lorex (main stream)",
        url_template=(
            "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype=0"
        ),
        notes_key="dahua",
    ),
    CameraPreset(
        key="dahua_sub",
        label="Dahua / Amcrest / Lorex (substream)",
        url_template=(
            "rtsp://{user}:{password}@{ip}:{port}/cam/realmonitor?channel={channel}&subtype=1"
        ),
        notes_key="dahua",
    ),
    CameraPreset(
        key="reolink",
        label="Reolink",
        url_template="rtsp://{user}:{password}@{ip}:{port}/h264Preview_{channel:02d}_main",
        notes_key="reolink",
    ),
    CameraPreset(
        key="tplink",
        label="TP-Link Tapo / Vigi",
        url_template="rtsp://{user}:{password}@{ip}:{port}/stream1",
        notes_key="tplink",
    ),
    CameraPreset(
        key="uniview",
        label="Uniview (UNV)",
        url_template="rtsp://{user}:{password}@{ip}:{port}/media/video1",
        notes_key="uniview",
    ),
    CameraPreset(
        key="axis",
        label="Axis",
        url_template="rtsp://{user}:{password}@{ip}:{port}/axis-media/media.amp",
        notes_key="axis",
    ),
    CameraPreset(
        key="generic",
        label="Other (manual RTSP URL)",
        url_template=None,
        notes_key="generic",
    ),
)

_PRESETS_BY_KEY = {preset.key: preset for preset in PRESETS}


def get_preset(key: str) -> CameraPreset:
    """Look up a preset; raises ``ValueError`` for unknown keys."""
    preset = _PRESETS_BY_KEY.get(key)
    if preset is None:
        raise ValueError(f"unknown camera preset: {key!r}")
    return preset


def build_rtsp_url(
    preset_key: str,
    ip: str,
    user: str = "",
    password: str = "",
    port: int | None = None,
    channel: int = 1,
) -> str:
    """Build the vendor RTSP URL from wizard fields.

    Credentials are percent-encoded (``quote(..., safe="")``); an empty
    username drops the ``user:password@`` part entirely. Validates the
    host, port (1-65535) and channel (>= 1).
    """
    preset = get_preset(preset_key)
    if preset.url_template is None:
        raise ValueError(f"preset {preset_key!r} takes a manual URL, not preset fields")
    ip = ip.strip()
    if not ip:
        raise ValueError("ip/host must not be empty")
    if any(ch in ip for ch in "/@:?# "):
        raise ValueError(f"ip/host contains invalid characters: {ip!r}")
    if port is None:
        port = preset.default_port
    if not 1 <= port <= 65535:
        raise ValueError(f"port must be 1-65535, got {port}")
    if channel < 1:
        raise ValueError(f"channel must be >= 1, got {channel}")
    template = preset.url_template
    if not user:
        if password:
            raise ValueError("password given without a username")
        template = template.replace("{user}:{password}@", "")
    return template.format(
        user=quote(user, safe=""),
        password=quote(password, safe=""),
        ip=ip,
        port=port,
        channel=channel,
    )


_CREDENTIALS_RE = re.compile(r"^(?P<scheme>[a-z][a-z0-9+.-]*://)(?P<user>[^/@]*):(?P<pw>[^/@]*)@")


def mask_url_password(url: str) -> str:
    """Replace the password in ``scheme://user:password@...`` with a mask.

    URLs without credentials are returned unchanged. Used whenever a source
    URL is echoed back to the client — the real password must never leave
    the server in a probe response.
    """
    return _CREDENTIALS_RE.sub(rf"\g<scheme>\g<user>:{PASSWORD_MASK}@", url)
