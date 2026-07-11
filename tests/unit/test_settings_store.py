"""settings_store: RefinerSettings persistence in app_settings + precedence
(dashboard row overrides config.yaml; broken rows/tables fall back safely)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.orm import Session, sessionmaker

from toskana.config import AppConfig
from toskana.db.base import Base, make_engine, make_session_factory
from toskana.db.models import AppSetting
from toskana.settings_store import (
    REFINER_SETTINGS_KEY,
    RefinerSettings,
    effective_refiner_settings,
    load_refiner_settings,
    refiner_settings_from_config,
    save_refiner_settings,
)


@pytest.fixture()
def session_factory(tmp_path: Path) -> sessionmaker[Session]:
    engine = make_engine(str(tmp_path / "settings.db"))
    Base.metadata.create_all(engine)
    return make_session_factory(engine)


class TestRoundtrip:
    def test_save_then_load_returns_equal_settings(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        settings = RefinerSettings(
            provider="openai_compatible",
            model="qwen2.5vl",
            base_url="http://localhost:1234/v1",
            api_key="sk-local",
            only_below_confidence=0.65,
            match_menu_items=False,
            max_per_minute=12,
        )
        with session_factory() as session:
            save_refiner_settings(session, settings)
            session.commit()
        with session_factory() as session:
            assert load_refiner_settings(session) == settings

    def test_save_twice_upserts_single_row(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as session:
            save_refiner_settings(session, RefinerSettings(provider="anthropic"))
            session.commit()
            save_refiner_settings(session, RefinerSettings(provider="off", model="x"))
            session.commit()
            rows = session.query(AppSetting).all()
            assert len(rows) == 1 and rows[0].key == REFINER_SETTINGS_KEY
        with session_factory() as session:
            loaded = load_refiner_settings(session)
            assert loaded is not None and loaded.provider == "off" and loaded.model == "x"

    def test_load_returns_none_without_row(self, session_factory: sessionmaker[Session]) -> None:
        with session_factory() as session:
            assert load_refiner_settings(session) is None

    def test_broken_json_row_falls_back_to_none(
        self, session_factory: sessionmaker[Session]
    ) -> None:
        with session_factory() as session:
            session.add(AppSetting(key=REFINER_SETTINGS_KEY, value="{not json"))
            session.commit()
        with session_factory() as session:
            assert load_refiner_settings(session) is None


class TestPrecedence:
    def test_config_values_apply_without_db_row(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        config = AppConfig(
            db_path=str(tmp_path / "settings.db"),
            refiner_provider="anthropic",
            refiner_model="claude-x",
            refiner_api_key="cfg-key",
            refiner_only_below_confidence=0.7,
        )
        effective = effective_refiner_settings(session_factory, config)
        assert effective == refiner_settings_from_config(config)
        assert effective.provider == "anthropic"
        assert effective.model == "claude-x"
        assert effective.api_key == "cfg-key"
        assert effective.only_below_confidence == 0.7

    def test_db_row_overrides_config(
        self, session_factory: sessionmaker[Session], tmp_path: Path
    ) -> None:
        config = AppConfig(
            db_path=str(tmp_path / "settings.db"),
            refiner_provider="anthropic",
            refiner_model="claude-x",
        )
        stored = RefinerSettings(provider="openai_compatible", model="llava", max_per_minute=5)
        with session_factory() as session:
            save_refiner_settings(session, stored)
            session.commit()
        assert effective_refiner_settings(session_factory, config) == stored

    def test_missing_table_falls_back_to_config(self, tmp_path: Path) -> None:
        # A DB without the app_settings table (not yet migrated).
        engine = make_engine(str(tmp_path / "empty.db"))
        factory = make_session_factory(engine)
        config = AppConfig(db_path=str(tmp_path / "empty.db"), refiner_provider="anthropic")
        effective = effective_refiner_settings(factory, config)
        assert effective.provider == "anthropic"
