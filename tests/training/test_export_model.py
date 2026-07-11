"""export_model.py registry integration (dummy weights — no model loading)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import Engine, select

from toskana.db.models import Category, ClassMapping, ModelRegistry
from toskana.db.seed import seed
from training import export_model


@pytest.fixture()
def seeded_db(engine: Engine, session, tmp_path: Path) -> Path:
    seed(session)
    return tmp_path / "test.db"  # path used by tests/conftest.py's engine fixture


@pytest.fixture()
def dummy_weights(tmp_path: Path) -> Path:
    weights = tmp_path / "best.pt"
    weights.write_bytes(b"not a real torch checkpoint")
    return weights


def _export(db: Path, weights: Path, tmp_path: Path, *extra: str) -> int:
    argv = [
        "--weights",
        str(weights),
        "--restaurant",
        "toskana",
        "--name",
        "smoke",
        "--version",
        "1",
        "--db",
        str(db),
        "--models-dir",
        str(tmp_path / "models"),
        *extra,
    ]
    return export_model.main(argv)


def test_export_registers_model_and_mappings(
    seeded_db: Path, dummy_weights: Path, tmp_path: Path, session
) -> None:
    metrics_path = tmp_path / "metrics.json"
    metrics_path.write_text(json.dumps({"map50": 0.42, "map50_95": 0.21}))
    rc = _export(
        seeded_db,
        dummy_weights,
        tmp_path,
        "--classes",
        "drink,main,dessert",
        "--metrics",
        str(metrics_path),
    )
    assert rc == 0

    dest = tmp_path / "models" / "toskana" / "smoke-1"
    assert (dest / "best.pt").read_bytes() == dummy_weights.read_bytes()
    meta = json.loads((dest / "meta.json").read_text())
    assert meta["classes"] == ["drink", "main", "dessert"]
    assert meta["metrics"]["map50"] == 0.42
    assert meta["kind"] == "finetuned"
    assert len(meta["git_sha"]) == 40

    model = session.scalar(select(ModelRegistry).where(ModelRegistry.name == "smoke"))
    assert model is not None
    assert model.kind == "finetuned"
    assert model.is_active is False
    assert model.version == "1"
    assert json.loads(model.classes_json) == ["drink", "main", "dessert"]
    assert json.loads(model.metrics_json)["map50"] == 0.42
    assert model.path == str(dest / "best.pt")

    mappings = session.scalars(select(ClassMapping).where(ClassMapping.model_id == model.id)).all()
    # class names equal seeded category keys -> auto-resolved targets
    by_class = {m.model_class_name: m for m in mappings}
    assert set(by_class) == {"drink", "main", "dessert"}
    categories = {
        c.id: c.key for c in session.scalars(select(Category).where(Category.key.in_(by_class)))
    }
    for name, mapping in by_class.items():
        assert mapping.model_class_id == ["drink", "main", "dessert"].index(name)
        assert categories[mapping.category_id] == name
        assert mapping.menu_item_id is None


def test_export_is_idempotent(seeded_db: Path, dummy_weights: Path, tmp_path: Path, session):
    args = ("--classes", "drink,main,dessert")
    assert _export(seeded_db, dummy_weights, tmp_path, *args) == 0
    assert _export(seeded_db, dummy_weights, tmp_path, *args) == 0
    models = session.scalars(select(ModelRegistry).where(ModelRegistry.name == "smoke")).all()
    assert len(models) == 1
    mappings = session.scalars(
        select(ClassMapping).where(ClassMapping.model_id == models[0].id)
    ).all()
    assert len(mappings) == 3


def test_export_explicit_and_unresolvable_mappings(
    seeded_db: Path, dummy_weights: Path, tmp_path: Path, session, capsys
) -> None:
    rc = _export(
        seeded_db,
        dummy_weights,
        tmp_path,
        "--classes",
        "cup,mystery",
        "--map-category",
        "cup=drink",
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "mystery" in out  # reported for the admin to complete in the dashboard
    model = session.scalar(select(ModelRegistry).where(ModelRegistry.name == "smoke"))
    mappings = session.scalars(select(ClassMapping).where(ClassMapping.model_id == model.id)).all()
    assert len(mappings) == 1  # only the explicitly mapped class gets a skeleton row
    assert mappings[0].model_class_name == "cup"
    drink = session.scalar(select(Category).where(Category.key == "drink"))
    assert mappings[0].category_id == drink.id


def test_export_unknown_restaurant_or_category_fails(
    seeded_db: Path, dummy_weights: Path, tmp_path: Path
) -> None:
    rc = export_model.main(
        [
            "--weights",
            str(dummy_weights),
            "--restaurant",
            "does-not-exist",
            "--name",
            "x",
            "--version",
            "1",
            "--db",
            str(seeded_db),
            "--models-dir",
            str(tmp_path / "models"),
            "--classes",
            "drink",
        ]
    )
    assert rc == 2
    rc = _export(
        seeded_db, dummy_weights, tmp_path, "--classes", "cup", "--map-category", "cup=nope"
    )
    assert rc == 2
