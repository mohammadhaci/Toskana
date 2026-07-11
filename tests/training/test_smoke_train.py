"""@slow smoke train: full chain -> 2-epoch CPU training -> export -> reload.

Trains yolov8n **from scratch** (``yolov8n.yaml``, random init — works
offline, no pretrained weights) on ~30 synthetic frames covering all three
classes, evaluates, exports into the registry and finally loads the exported
weights in :class:`YoloBackend` to prove the deployed artifact is usable.
Accuracy is irrelevant here (near-random mAP is expected).

Run with: pytest -q -m slow tests/training/
"""

from __future__ import annotations

import json
from pathlib import Path

import cv2
import pytest
from sqlalchemy import select

from tests.tools import make_synthetic_video as msv
from tests.tools.scenarios import ObjectSpec, ScenarioSpec, Waypoint
from toskana.db.base import make_engine, make_session_factory
from toskana.db.models import ClassMapping, ModelRegistry
from toskana.db.seed import seed
from training import (
    autolabel,
    build_dataset,
    evaluate,
    export_model,
    extract_frames,
    ingest_local,
    train,
    validate_labels,
)

pytestmark = pytest.mark.slow


def _smoke_mix_spec() -> ScenarioSpec:
    """60-frame clip with one object of each class (drink/main/dessert)."""
    objects = (
        ObjectSpec("drink_1", "drink", "circle", 36, (Waypoint(0, 60, 80), Waypoint(59, 580, 80))),
        ObjectSpec("main_1", "main", "rect", 44, (Waypoint(0, 580, 180), Waypoint(59, 60, 180))),
        ObjectSpec(
            "dessert_1", "dessert", "circle", 40, (Waypoint(0, 60, 280), Waypoint(59, 580, 280))
        ),
    )
    return ScenarioSpec("smoke_mix", 60, objects, ())


@pytest.fixture(scope="module")
def work(tmp_path_factory: pytest.TempPathFactory) -> Path:
    return tmp_path_factory.mktemp("smoke_train")


def test_full_smoke_train_chain(work: Path) -> None:
    # --- clips: custom 3-class mix + the tray scenario (2 source videos) ---
    clips_dir = work / "clips"
    mix_video = msv.write_video(_smoke_mix_spec(), clips_dir / "smoke_mix")
    tray = msv.generate_scenario("tray_carry_3", clips_dir)

    raw = work / "raw"
    rc = ingest_local.main(
        ["--videos", str(mix_video), str(tray.video_paths["cam"]), "--out", str(raw)]
    )
    assert rc == 0

    frames = work / "frames"
    rc = extract_frames.main(
        ["--raw", str(raw), "--out", str(frames), "--interval-s", "0.25", "--phash-threshold", "-1"]
    )
    assert rc == 0
    images = list(frames.rglob("*.jpg"))
    assert 25 <= len(images) <= 40  # ~30 images, 2 videos

    labels = work / "labels"
    rc = autolabel.main(["--frames", str(frames), "--out", str(labels), "--backend", "synthetic"])
    assert rc == 0
    assert validate_labels.main(["--labels", str(labels), "--images", str(frames)]) == 0

    dataset = work / "dataset"
    rc = build_dataset.main(
        [
            "--frames", str(frames), "--labels", str(labels), "--out", str(dataset),
            "--classes", "drink,main,dessert", "--val-ratio", "0.4",
        ]
    )  # fmt: skip
    assert rc == 0

    # --- train from scratch on CPU (2 epochs, tiny imgsz) -------------------
    run_dir = work / "runs" / "smoke"
    rc = train.main(
        [
            "--data", str(dataset / "data.yaml"), "--base", "yolov8n.yaml",
            "--epochs", "2", "--imgsz", "160", "--device", "cpu", "--out", str(run_dir),
        ]
    )  # fmt: skip
    assert rc == 0
    best = run_dir / "weights" / "best.pt"
    assert best.is_file()
    assert json.loads((run_dir / "manifest.json").read_text())["stage"] == "train"

    # --- evaluate: metrics.json written, gate at 0.0 passes ----------------
    metrics_path = run_dir / "metrics.json"
    rc = evaluate.main(
        [
            "--weights", str(best), "--data", str(dataset / "data.yaml"),
            "--imgsz", "160", "--device", "cpu", "--out", str(metrics_path),
        ]
    )  # fmt: skip
    assert rc == 0
    metrics = json.loads(metrics_path.read_text())
    assert set(metrics["per_class"]) == {"drink", "main", "dessert"}
    assert 0.0 <= metrics["map50"] <= 1.0  # near-random is fine; pipeline proof
    print(f"smoke-train mAP50={metrics['map50']} mAP50-95={metrics['map50_95']}")

    # --- export into a seeded registry DB -----------------------------------
    db_path = work / "toskana.db"
    engine = make_engine(db_path)
    from toskana.db.base import Base

    Base.metadata.create_all(engine)
    with make_session_factory(engine)() as session:
        seed(session)
    rc = export_model.main(
        [
            "--weights", str(best), "--restaurant", "toskana", "--name", "smoke",
            "--version", "1", "--db", str(db_path), "--metrics", str(metrics_path),
            "--dataset", str(dataset), "--models-dir", str(work / "models"),
        ]
    )  # fmt: skip
    assert rc == 0
    exported = work / "models" / "toskana" / "smoke-1" / "best.pt"
    assert exported.is_file()
    meta = json.loads((exported.parent / "meta.json").read_text())
    assert meta["classes"] == ["drink", "main", "dessert"]  # read from the weights
    assert meta["dataset_manifest"]["stage"] == "build_dataset"
    # license chain reaches back to the ingest manifest
    ingest_manifest = meta["dataset_manifest"]["source_manifests"]["frames"]["source_manifest"]
    assert {v["license"] for v in ingest_manifest["videos"]} == {"local"}

    with make_session_factory(engine)() as session:
        model = session.scalar(select(ModelRegistry).where(ModelRegistry.name == "smoke"))
        assert model is not None
        assert model.kind == "finetuned" and model.is_active is False
        assert json.loads(model.classes_json) == ["drink", "main", "dessert"]
        mappings = session.scalars(
            select(ClassMapping).where(ClassMapping.model_id == model.id)
        ).all()
        assert {m.model_class_name for m in mappings} == {"drink", "main", "dessert"}
        model_path = model.path
    engine.dispose()

    # --- the exported artifact loads in the runtime backend -----------------
    from toskana.vision.backends.yolo import YoloBackend

    backend = YoloBackend(model_path, device="cpu", imgsz=160, conf=0.01)
    assert backend.class_names == ["drink", "main", "dessert"]
    sample = next(iter((dataset / "images" / "val").glob("*.jpg")), None)
    if sample is None:
        sample = next(iter((dataset / "images" / "train").glob("*.jpg")))
    frame = cv2.imread(str(sample))
    detections = backend.detect(frame)
    assert isinstance(detections, list)  # any output (incl. none) is fine after 2 epochs
