"""Performance presets: sane YOLO settings per hardware class.

``performance_preset`` in the config selects one of:

* ``cpu``  — imgsz 480, process every 2nd frame (``frame_skip=2``);
* ``gpu``  — imgsz 640, every frame;
* ``auto`` — ``gpu`` when CUDA is available, else ``cpu`` (default).

Presets apply to the YOLO backend only; the synthetic (CI/demo) backend
always processes every frame so scenario counts stay deterministic. The
manager logs the measured FPS ~5 s after each camera starts and warns below
:data:`MIN_HEALTHY_FPS`.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

PRESET_NAMES = ("cpu", "gpu", "auto")

#: Below this measured FPS the counting accuracy degrades noticeably.
MIN_HEALTHY_FPS = 8.0


@dataclass(frozen=True)
class PerformancePreset:
    """Resolved pipeline tuning for one hardware class."""

    name: str
    imgsz: int
    frame_skip: int


CPU_PRESET = PerformancePreset(name="cpu", imgsz=480, frame_skip=2)
GPU_PRESET = PerformancePreset(name="gpu", imgsz=640, frame_skip=1)


def cuda_available() -> bool:
    """CUDA present and usable (False without torch or on any probe error)."""
    if importlib.util.find_spec("torch") is None:
        return False
    try:
        import torch

        return bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001 - a broken torch install means no CUDA
        return False


def resolve_preset(configured: str, *, cuda: bool | None = None) -> PerformancePreset:
    """Turn the configured preset name into concrete settings.

    ``cuda`` overrides autodetection (tests); ``auto`` resolves to ``gpu``
    when CUDA is available, else ``cpu``.
    """
    name = configured.strip().lower()
    if name not in PRESET_NAMES:
        raise ValueError(f"performance_preset must be one of {PRESET_NAMES}: {configured!r}")
    if name == "auto":
        name = "gpu" if (cuda if cuda is not None else cuda_available()) else "cpu"
    return GPU_PRESET if name == "gpu" else CPU_PRESET
