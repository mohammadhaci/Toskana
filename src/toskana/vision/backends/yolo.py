"""Ultralytics YOLO detection/tracking backend (optional ``[ml]`` extra).

The ``ultralytics``/``torch`` imports are lazy (inside ``__init__``) so the
core package imports without ML dependencies installed. Tracking uses
ByteTrack via ``model.track(persist=True)``.
"""

from __future__ import annotations

from os import PathLike
from typing import Any

import numpy as np

from toskana.vision.detector import Detection, TrackedDetection


def select_device(preference: str = "auto") -> str:
    """Pick a torch device: explicit preference, else cuda > mps > cpu."""
    if preference != "auto":
        return preference
    try:
        import torch
    except ImportError:
        return "cpu"
    if torch.cuda.is_available():
        return "cuda"
    mps = getattr(torch.backends, "mps", None)
    if mps is not None and mps.is_available():
        return "mps"
    return "cpu"


class YoloBackend:
    """YOLO detector + ByteTrack tracker behind the common protocols.

    ``weights`` may be a stock name (``yolov8n.pt``) or a path to a ``.pt``
    file from the models registry directory.
    """

    def __init__(
        self,
        weights: str | PathLike[str] = "yolov8n.pt",
        *,
        device: str = "auto",
        imgsz: int = 640,
        conf: float = 0.35,
        tracker: str = "bytetrack.yaml",
    ) -> None:
        try:
            from ultralytics import YOLO
        except ImportError as exc:  # pragma: no cover - exercised without [ml]
            raise RuntimeError(
                "ultralytics is not installed; install the ML extra: pip install 'toskana[ml]'"
            ) from exc
        self.weights_path = str(weights)
        self.device = select_device(device)
        self.imgsz = imgsz
        self.conf = conf
        self.tracker = tracker
        self._model = YOLO(self.weights_path)

    @property
    def class_names(self) -> list[str]:
        names = self._model.names
        if isinstance(names, dict):
            return [str(names[key]) for key in sorted(names)]
        return [str(name) for name in names]

    def detect(self, frame: np.ndarray) -> list[Detection]:
        results = self._model.predict(
            frame,
            imgsz=self.imgsz,
            conf=self.conf,
            device=self.device,
            verbose=False,
        )
        return [det for det, _ in self._iter_boxes(results[0])]

    def detect_and_track(self, frame: np.ndarray) -> list[TrackedDetection]:
        results = self._model.track(
            frame,
            persist=True,
            verbose=False,
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            tracker=self.tracker,
        )
        tracked: list[TrackedDetection] = []
        for det, track_id in self._iter_boxes(results[0]):
            if track_id is None:
                continue  # tracker has not confirmed this box yet
            tracked.append(
                TrackedDetection(
                    x1=det.x1,
                    y1=det.y1,
                    x2=det.x2,
                    y2=det.y2,
                    class_id=det.class_id,
                    class_name=det.class_name,
                    confidence=det.confidence,
                    track_id=track_id,
                )
            )
        return tracked

    def _iter_boxes(self, result: Any) -> list[tuple[Detection, int | None]]:
        boxes = result.boxes
        if boxes is None:
            return []
        names = result.names
        out: list[tuple[Detection, int | None]] = []
        for i in range(len(boxes)):
            x1, y1, x2, y2 = (float(v) for v in boxes.xyxy[i].tolist())
            class_id = int(boxes.cls[i])
            confidence = float(boxes.conf[i])
            track_id = int(boxes.id[i]) if boxes.id is not None else None
            out.append(
                (
                    Detection(
                        x1=x1,
                        y1=y1,
                        x2=x2,
                        y2=y2,
                        class_id=class_id,
                        class_name=str(names.get(class_id, str(class_id))),
                        confidence=confidence,
                    ),
                    track_id,
                )
            )
        return out
