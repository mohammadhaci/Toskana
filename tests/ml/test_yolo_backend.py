"""Real-YOLO backend tests. Marked ``ml``: excluded from default CI runs
(``addopts = -m "not ml and not slow"``); run with ``pytest -m ml``.

Robust by design: missing ultralytics, unavailable weights (offline) or
missing bundled assets skip gracefully instead of failing.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = pytest.mark.ml


def _load_backend():  # -> YoloBackend
    pytest.importorskip("ultralytics")
    from toskana.vision.backends.yolo import YoloBackend

    try:
        # yolov8n auto-downloads on first use; skip when the network is cut.
        return YoloBackend("yolov8n.pt", device="auto", imgsz=320, conf=0.25)
    except Exception as exc:  # noqa: BLE001 - any load/download failure -> skip
        pytest.skip(f"yolov8n weights unavailable (offline?): {exc}")


def test_device_auto_selection() -> None:
    torch = pytest.importorskip("torch")
    from toskana.vision.backends.yolo import select_device

    device = select_device("auto")
    assert device in {"cpu", "cuda", "mps"}
    mps = getattr(torch.backends, "mps", None)
    if not torch.cuda.is_available() and not (mps is not None and mps.is_available()):
        assert device == "cpu"  # this environment has no GPU
    assert select_device("cpu") == "cpu"  # explicit override wins


def test_backend_constructs_and_exposes_classes() -> None:
    backend = _load_backend()
    assert backend.device in {"cpu", "cuda", "mps"}
    names = backend.class_names
    assert len(names) == 80  # COCO
    assert "person" in names
    assert "cup" in names


def test_detects_something_on_bundled_image() -> None:
    import cv2

    backend = _load_backend()
    try:
        from ultralytics.utils import ASSETS
    except ImportError:
        pytest.skip("ultralytics.utils.ASSETS unavailable")
    image_path = Path(ASSETS) / "bus.jpg"
    if not image_path.is_file():
        pytest.skip(f"bundled test image missing: {image_path}")
    frame = cv2.imread(str(image_path))
    assert frame is not None

    detections = backend.detect(frame)
    assert detections, "yolov8n should detect something on the bundled bus image"
    assert all(d.confidence >= 0.25 for d in detections)
    assert all(d.x2 > d.x1 and d.y2 > d.y1 for d in detections)

    tracked = backend.detect_and_track(frame)
    assert tracked, "ByteTrack should confirm at least one track"
    assert all(t.track_id >= 1 for t in tracked)
