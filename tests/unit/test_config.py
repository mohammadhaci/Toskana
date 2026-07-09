"""Config loading: defaults, YAML file, env-var overrides, precedence."""

from __future__ import annotations

from pathlib import Path

import pytest

from toskana.config import AppConfig, load_config


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in list(__import__("os").environ):
        if var.startswith("TOSKANA_"):
            monkeypatch.delenv(var, raising=False)


def test_defaults(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)  # no config.yaml here
    cfg = load_config()
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8420
    assert cfg.db_path == "./toskana.db"
    assert cfg.snapshots_dir == "./data/snapshots"
    assert cfg.snapshot_retention_days == 30
    assert cfg.device == "auto"


def test_yaml_file_loaded(tmp_path: Path) -> None:
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("port: 9000\nactive_restaurant_slug: pizzeria\n")
    cfg = load_config(cfg_file)
    assert cfg.port == 9000
    assert cfg.active_restaurant_slug == "pizzeria"
    assert cfg.host == "127.0.0.1"  # untouched default


def test_yaml_path_from_env_var(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "via-env.yaml"
    cfg_file.write_text("db_path: /srv/toskana/site.db\n")
    monkeypatch.setenv("TOSKANA_CONFIG", str(cfg_file))
    cfg = load_config()
    assert cfg.db_path == "/srv/toskana/site.db"


def test_env_overrides_yaml(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_file = tmp_path / "c.yaml"
    cfg_file.write_text("port: 9000\nlog_level: DEBUG\n")
    monkeypatch.setenv("TOSKANA_PORT", "9999")
    cfg = load_config(cfg_file)
    assert cfg.port == 9999  # env wins
    assert cfg.log_level == "DEBUG"  # yaml still applies


def test_missing_explicit_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.yaml")


def test_non_mapping_yaml_rejected(tmp_path: Path) -> None:
    cfg_file = tmp_path / "bad.yaml"
    cfg_file.write_text("- just\n- a list\n")
    with pytest.raises(ValueError):
        load_config(cfg_file)


def test_db_url_property() -> None:
    assert AppConfig(db_path=":memory:").db_url == "sqlite+pysqlite:///:memory:"
    assert AppConfig(db_path="./x.db").db_url.startswith("sqlite+pysqlite:///")
