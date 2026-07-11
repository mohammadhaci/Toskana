"""CI chain over the training stages (no weights, no network).

Generates deterministic synthetic scenario clips, then runs
ingest_local -> extract_frames -> autolabel(synthetic) -> validate_labels ->
build_dataset and asserts artifacts, manifests (with git SHA), phash
deduplication and the train/val leakage guard.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from tests.tools import make_synthetic_video as msv
from training import autolabel, build_dataset, extract_frames, ingest_local, validate_labels
from training._common import git_sha, read_yolo_labels

FRAME_RE = re.compile(r"_f(\d{6})$")


def _load_manifest(directory: Path) -> dict:
    path = directory / "manifest.json"
    assert path.is_file(), f"manifest.json missing in {directory}"
    return json.loads(path.read_text(encoding="utf-8"))


def _assert_manifest(directory: Path, stage: str) -> dict:
    manifest = _load_manifest(directory)
    assert manifest["stage"] == stage
    assert manifest["git_sha"] == git_sha()
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["git_sha"])
    return manifest


@pytest.fixture(scope="module")
def clips(tmp_path_factory: pytest.TempPathFactory) -> dict[str, msv.GeneratedScenario]:
    out = tmp_path_factory.mktemp("clips")
    return {
        name: msv.generate_scenario(name, out)
        for name in ("single_drink", "tray_carry_3", "loiter_on_line")
    }


@pytest.fixture(scope="module")
def raw_dir(clips, tmp_path_factory: pytest.TempPathFactory) -> Path:
    raw = tmp_path_factory.mktemp("raw")
    videos = [
        str(clips["single_drink"].video_paths["cam"]),
        str(clips["tray_carry_3"].video_paths["cam"]),
    ]
    assert ingest_local.main(["--videos", *videos, "--out", str(raw)]) == 0
    return raw


@pytest.fixture(scope="module")
def frames_dir(raw_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    frames = tmp_path_factory.mktemp("frames")
    argv = ["--raw", str(raw_dir), "--out", str(frames)]
    assert extract_frames.main([*argv, "--interval-s", "0.2", "--phash-threshold", "-1"]) == 0
    return frames


@pytest.fixture(scope="module")
def labels_dir(frames_dir: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    labels = tmp_path_factory.mktemp("labels")
    argv = ["--frames", str(frames_dir), "--out", str(labels), "--backend", "synthetic"]
    assert autolabel.main(argv) == 0
    return labels


@pytest.fixture(scope="module")
def dataset_dir(
    frames_dir: Path, labels_dir: Path, tmp_path_factory: pytest.TempPathFactory
) -> Path:
    dataset = tmp_path_factory.mktemp("dataset")
    argv = [
        "--frames",
        str(frames_dir),
        "--labels",
        str(labels_dir),
        "--out",
        str(dataset),
        "--classes",
        "drink,main,dessert",
        "--val-ratio",
        "0.4",
    ]
    assert build_dataset.main(argv) == 0
    return dataset


# --------------------------------------------------------------------------
# ingest_local
# --------------------------------------------------------------------------


def test_ingest_local_copies_videos_with_manifest(raw_dir: Path) -> None:
    videos = sorted(p.name for p in raw_dir.iterdir() if p.suffix in (".mp4", ".avi"))
    assert len(videos) == 2
    manifest = _assert_manifest(raw_dir, "ingest")
    entries = {v["file"]: v for v in manifest["videos"]}
    assert set(entries) == set(videos)
    for entry in entries.values():
        assert entry["license"] == "local"
        assert Path(entry["source_path"]).is_absolute()


def test_ingest_local_rejects_missing_file(tmp_path: Path) -> None:
    rc = ingest_local.main(["--videos", str(tmp_path / "nope.mp4"), "--out", str(tmp_path / "o")])
    assert rc == 1


# --------------------------------------------------------------------------
# extract_frames
# --------------------------------------------------------------------------


def test_extract_frames_per_video_subdirs(frames_dir: Path) -> None:
    for video in ("single_drink", "tray_carry_3"):
        frames = sorted((frames_dir / video).glob("*.jpg"))
        # 60 frames @15fps sampled every 0.2 s (step 3) -> 20 frames
        assert len(frames) == 20
        for frame in frames:
            match = FRAME_RE.search(frame.stem)
            assert match, frame.name
            image = cv2.imread(str(frame))
            assert image is not None and image.shape == (360, 640, 3)
    manifest = _assert_manifest(frames_dir, "extract_frames")
    assert manifest["source_manifest"]["stage"] == "ingest"
    assert {v["video"].split(".")[0] for v in manifest["videos"]} == {
        "single_drink",
        "tray_carry_3",
    }


def test_phash_dedup_drops_near_duplicates(clips, tmp_path_factory: pytest.TempPathFactory) -> None:
    """The loitering clip wiggles +-8 px for ~2 s -> near-identical samples."""
    raw = tmp_path_factory.mktemp("loiter_raw")
    video = str(clips["loiter_on_line"].video_paths["cam"])
    assert ingest_local.main(["--videos", video, "--out", str(raw)]) == 0

    def kept(threshold: int) -> int:
        out = tmp_path_factory.mktemp(f"loiter_frames_{threshold}")
        argv = ["--raw", str(raw), "--out", str(out), "--interval-s", "0.2"]
        assert extract_frames.main([*argv, "--phash-threshold", str(threshold)]) == 0
        return len(list(out.rglob("*.jpg")))

    kept_all = kept(-1)  # dedup disabled: every sampled frame kept
    kept_deduped = kept(8)
    assert kept_deduped < kept_all
    assert kept_deduped >= 2


# --------------------------------------------------------------------------
# autolabel (synthetic backend)
# --------------------------------------------------------------------------


def test_autolabel_writes_valid_yolo_labels(frames_dir: Path, labels_dir: Path) -> None:
    classes = (labels_dir / "classes.txt").read_text().split()
    assert classes == ["drink", "main", "dessert"]
    images = sorted(frames_dir.rglob("*.jpg"))
    assert images
    for image in images:
        label = labels_dir / image.relative_to(frames_dir).with_suffix(".txt")
        assert label.is_file(), f"missing label for {image}"
        for class_id, cx, cy, w, h in read_yolo_labels(label):
            assert 0 <= class_id < len(classes)
            for value in (cx, cy, w, h):
                assert 0.0 <= value <= 1.0
            assert w > 0 and h > 0
    manifest = _assert_manifest(labels_dir, "autolabel")
    assert manifest["params"]["backend"] == "synthetic"
    assert manifest["source_manifest"]["stage"] == "extract_frames"


def test_autolabel_boxes_match_ground_truth(clips, frames_dir: Path, labels_dir: Path) -> None:
    gt = clips["single_drink"].ground_truth
    obj = gt["objects"][0]
    assert obj["class_key"] == "drink"
    checked = 0
    for label in sorted((labels_dir / "single_drink").glob("*.txt")):
        match = FRAME_RE.search(label.stem)
        assert match
        gt_center = obj["path"].get(str(int(match.group(1))))
        if gt_center is None:
            continue
        boxes = read_yolo_labels(label)
        assert len(boxes) == 1
        class_id, cx, cy, w, h = boxes[0]
        assert class_id == 0  # drink
        assert abs(cx * 640 - gt_center[0]) <= 6
        assert abs(cy * 360 - gt_center[1]) <= 6
        assert 30 <= w * 640 <= 44  # circle of diameter 36 px
        checked += 1
    assert checked >= 10


def test_autolabel_review_queue_collects_low_confidence(frames_dir: Path, tmp_path: Path) -> None:
    out = tmp_path / "labels_review"
    argv = ["--frames", str(frames_dir), "--out", str(out), "--backend", "synthetic"]
    assert autolabel.main([*argv, "--review-conf", "1.0"]) == 0
    queued = [line for line in (out / "review_queue.txt").read_text().splitlines() if line]
    # synthetic confidence is 0.99 < 1.0 -> every image needs review
    assert len(queued) == len(list(frames_dir.rglob("*.jpg")))


# --------------------------------------------------------------------------
# validate_labels
# --------------------------------------------------------------------------


def test_validate_labels_passes_on_autolabel_output(frames_dir: Path, labels_dir: Path) -> None:
    assert validate_labels.main(["--labels", str(labels_dir), "--images", str(frames_dir)]) == 0


def test_validate_labels_fails_on_bad_and_orphan_labels(tmp_path: Path, capsys) -> None:
    images = tmp_path / "images" / "vid"
    labels = tmp_path / "labels" / "vid"
    images.mkdir(parents=True)
    labels.mkdir(parents=True)
    cv2.imwrite(str(images / "a.jpg"), np.zeros((36, 64, 3), np.uint8))
    (tmp_path / "labels" / "classes.txt").write_text("drink\n")
    (labels / "a.txt").write_text("9 0.5 0.5 1.5 0.1\n")  # bad class id + w > 1
    (labels / "orphan.txt").write_text("0 0.5 0.5 0.1 0.1\n")  # no matching image
    rc = validate_labels.main(
        ["--labels", str(tmp_path / "labels"), "--images", str(tmp_path / "images")]
    )
    assert rc == 1
    err = capsys.readouterr().err
    assert "out of range" in err
    assert "outside 0..1" in err
    assert "orphan label" in err


# --------------------------------------------------------------------------
# build_dataset
# --------------------------------------------------------------------------


def test_build_dataset_layout_and_split(frames_dir: Path, dataset_dir: Path) -> None:
    import yaml

    data = yaml.safe_load((dataset_dir / "data.yaml").read_text())
    assert data["names"] == ["drink", "main", "dessert"]
    assert data["nc"] == 3
    assert data["train"] == "images/train"
    assert data["val"] == "images/val"

    def videos_in(split: str) -> set[str]:
        images = list((dataset_dir / "images" / split).glob("*.jpg"))
        for image in images:
            assert (dataset_dir / "labels" / split / f"{image.stem}.txt").is_file()
        return {FRAME_RE.sub("", image.stem) for image in images}

    train_videos, val_videos = videos_in("train"), videos_in("val")
    # Leakage guard: each source video lands entirely in exactly one split.
    assert train_videos and val_videos
    assert train_videos.isdisjoint(val_videos)
    assert train_videos | val_videos == {"single_drink", "tray_carry_3"}

    total = len(list(frames_dir.rglob("*.jpg")))
    n_train = len(list((dataset_dir / "images" / "train").glob("*.jpg")))
    n_val = len(list((dataset_dir / "images" / "val").glob("*.jpg")))
    assert n_train + n_val == total

    manifest = _assert_manifest(dataset_dir, "build_dataset")
    assert set(manifest["video_splits"].values()) == {"train", "val"}
    assert manifest["image_counts"] == {"train": n_train, "val": n_val}
    # tray_carry_3 has 1 drink + 2 mains per frame; single_drink 1 drink.
    combined = {
        name: manifest["class_counts"]["train"][name] + manifest["class_counts"]["val"][name]
        for name in ("drink", "main", "dessert")
    }
    assert combined["drink"] == 2 * 20
    assert combined["main"] == 2 * 20
    assert combined["dessert"] == 0
    assert manifest["source_manifests"]["frames"]["stage"] == "extract_frames"
    assert manifest["source_manifests"]["labels"]["stage"] == "autolabel"
