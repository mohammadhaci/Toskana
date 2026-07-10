"""Run the counting pipeline over annotated clips and score it vs ground truth.

Everything runs unpaced and deterministically. Timestamps are compared in
**media time** (``frame_index * 1000 / fps``, matching the generator's
``crossing_ts_ms``), so results are identical whether the clip is replayed
now or next week.

Two-camera mode replays both viewpoints through their own pipeline, then
feeds all raw crossings (sorted by media time) through a
:class:`~toskana.vision.dedup.DedupEngine` and evaluates only the canonical
survivors against the scenario's ``canonical_crossings``.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from toskana.config import AppConfig
from toskana.eval.counting import (
    DEFAULT_TOLERANCE_MS,
    GTCrossing,
    MeasuredCrossing,
    compute_report,
    counts_by_category,
    parse_gt_crossings,
)
from toskana.vision.line_crossing import CrossingEvent, LineSpec
from toskana.vision.pipeline import DIRECTION_TO_DB, CameraPipeline, PipelineSpec


@dataclass(frozen=True)
class EvalOutcome:
    """Everything one ``eval-counting`` run produced."""

    report: dict[str, Any]
    gt: list[GTCrossing]
    measured: list[MeasuredCrossing]  # canonical (post-dedup in two-cam mode)
    raw_measured_total: int  # before dedup (== canonical in single-cam mode)
    frames: dict[str, int]  # per-camera processed frame counts
    scenario: str | None = None


def _video_fps(video: str | Path, fallback: float) -> float:
    """Native FPS of the clip (cv2 probe), falling back to the GT value."""
    import cv2

    cap = cv2.VideoCapture(str(video))
    try:
        fps = float(cap.get(cv2.CAP_PROP_FPS)) if cap.isOpened() else 0.0
    finally:
        cap.release()
    return fps if fps > 0 else fallback


def _gt_fps(ground_truth: dict[str, Any], fallback: float = 15.0) -> float:
    """FPS recorded in the GT file (single-cam or first cam of two-cam)."""
    video = ground_truth.get("video")
    if isinstance(video, dict) and video.get("fps"):
        return float(video["fps"])
    cams = ground_truth.get("cams")
    if isinstance(cams, dict):
        for cam in cams.values():
            if isinstance(cam, dict) and isinstance(cam.get("video"), dict):
                fps = cam["video"].get("fps")
                if fps:
                    return float(fps)
    return fallback


def _run_pipeline(
    video: str | Path,
    line: LineSpec,
    *,
    camera_id: int,
    backend: str,
    model_path: str,
    device: str,
) -> tuple[list[CrossingEvent], int]:
    """Replay one clip unpaced; returns ``(crossings, processed_frames)``."""
    spec = PipelineSpec(
        restaurant_id=1,
        camera_id=camera_id,
        source=str(video),
        source_type="file",
        backend=backend,
        model_path=model_path,
        device=device,
        paced=False,
        lines=(line,),
    )
    result = CameraPipeline(spec).run_once()
    return list(result.crossings), result.frames


def _media_ts_ms(crossing: CrossingEvent, fps: float) -> int:
    return round(crossing.frame_index * 1000 / fps)


def _dedup_canonical(
    per_camera: list[tuple[int, list[CrossingEvent], float]],
    *,
    window_ms: int,
) -> tuple[list[MeasuredCrossing], int]:
    """Cross-camera dedup on media timestamps; returns (canonical, raw_total).

    Reuses the production :class:`~toskana.vision.dedup.DedupEngine` (greedy
    one-to-one, same direction + identity within the window) directly —
    no bus/threads, so the outcome is fully deterministic.
    """
    from toskana.events.writer import new_event_id
    from toskana.vision.dedup import DedupEngine, GroupConfig

    payloads: list[dict[str, Any]] = []
    for camera_id, crossings, fps in per_camera:
        for crossing in crossings:
            payloads.append(
                {
                    "id": new_event_id(),
                    "camera_id": camera_id,
                    "restaurant_id": 1,
                    "ts": _media_ts_ms(crossing, fps),
                    "direction": DIRECTION_TO_DB[crossing.direction],
                    "raw_class_name": crossing.class_name,
                    "category_id": None,
                    "menu_item_id": None,
                    "_class": crossing.class_name,
                    "_direction": crossing.direction,
                }
            )
    payloads.sort(key=lambda p: (p["ts"], p["camera_id"]))
    config = GroupConfig(group_id=1, window_ms=window_ms, strategy="first_wins")
    engine = DedupEngine({camera_id: config for camera_id, _, _ in per_camera})
    demoted: set[str] = set()
    for payload in payloads:
        match = engine.handle_crossing(payload)
        if match is not None:
            demoted.add(match.demoted_event_id)
    canonical = [
        MeasuredCrossing(
            category=p["_class"], direction=p["_direction"], ts_ms=p["ts"], event_id=p["id"]
        )
        for p in payloads
        if p["id"] not in demoted
    ]
    return canonical, len(payloads)


def evaluate_videos(
    video: str | Path,
    line: LineSpec,
    ground_truth: dict[str, Any],
    *,
    backend: str = "synthetic",
    video2: str | Path | None = None,
    line2: LineSpec | None = None,
    tolerance_ms: int = DEFAULT_TOLERANCE_MS,
    dedup_window_ms: int = 2000,
    model_path: str = "yolov8n.pt",
    device: str = "auto",
) -> EvalOutcome:
    """Replay the clip(s), match against GT and compute the metrics report."""
    gt = parse_gt_crossings(ground_truth)
    fps_fallback = _gt_fps(ground_truth)

    fps1 = _video_fps(video, fps_fallback)
    crossings1, frames1 = _run_pipeline(
        video, line, camera_id=1, backend=backend, model_path=model_path, device=device
    )
    frames: dict[str, int] = {"cam1": frames1}

    if video2 is not None:
        fps2 = _video_fps(video2, fps_fallback)
        crossings2, frames2 = _run_pipeline(
            video2,
            line2 if line2 is not None else line,
            camera_id=2,
            backend=backend,
            model_path=model_path,
            device=device,
        )
        frames["cam2"] = frames2
        measured, raw_total = _dedup_canonical(
            [(1, crossings1, fps1), (2, crossings2, fps2)], window_ms=dedup_window_ms
        )
    else:
        measured = [
            MeasuredCrossing(
                category=c.class_name, direction=c.direction, ts_ms=_media_ts_ms(c, fps1)
            )
            for c in crossings1
        ]
        raw_total = len(measured)

    report = compute_report(gt, measured, tolerance_ms=tolerance_ms)
    return EvalOutcome(
        report=report,
        gt=gt,
        measured=measured,
        raw_measured_total=raw_total,
        frames=frames,
        scenario=ground_truth.get("scenario"),
    )


@dataclass(frozen=True)
class EvalRunRecord:
    """What got persisted for one eval run (row id + payload for printing)."""

    run_id: int
    restaurant_id: int


def record_eval_run(
    db_path: str,
    config: AppConfig,
    outcome: EvalOutcome,
    *,
    video_ref: str,
    ground_truth_path: str,
    run_config: dict[str, Any],
    name: str | None = None,
    now_ms: int | None = None,
) -> EvalRunRecord:
    """Insert a ``counting_eval_runs`` row (creating schema/restaurant if needed)."""
    from sqlalchemy import select

    from toskana.db.base import Base, make_engine, make_session_factory
    from toskana.db.models import CountingEvalRun, Restaurant

    engine = make_engine(db_path)
    Base.metadata.create_all(engine)  # no-op on an initialized database
    try:
        with make_session_factory(engine)() as session:
            restaurant = session.scalar(
                select(Restaurant).where(Restaurant.slug == config.active_restaurant_slug)
            )
            if restaurant is None:
                restaurant = Restaurant(
                    slug=config.active_restaurant_slug, name=config.active_restaurant_slug
                )
                session.add(restaurant)
                session.flush()
            row = CountingEvalRun(
                restaurant_id=restaurant.id,
                created_ts=now_ms if now_ms is not None else int(time.time() * 1000),
                name=name or f"eval-counting {Path(video_ref.split(',')[0]).name}",
                scenario=outcome.scenario,
                ground_truth_path=ground_truth_path,
                video_ref=video_ref,
                config_json=json.dumps(run_config, sort_keys=True),
                gt_counts_json=json.dumps(counts_by_category(outcome.gt), sort_keys=True),
                measured_counts_json=json.dumps(
                    counts_by_category(outcome.measured), sort_keys=True
                ),
                metrics_json=json.dumps(outcome.report, sort_keys=True),
            )
            session.add(row)
            session.commit()
            return EvalRunRecord(run_id=row.id, restaurant_id=restaurant.id)
    finally:
        engine.dispose()
