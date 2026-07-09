from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from toskana.db.base import Base, make_engine, make_session_factory


@pytest.fixture()
def engine(tmp_path: Path) -> Iterator[Engine]:
    eng = make_engine(tmp_path / "test.db")
    Base.metadata.create_all(eng)
    yield eng
    eng.dispose()


@pytest.fixture()
def session(engine: Engine) -> Iterator[Session]:
    factory = make_session_factory(engine)
    with factory() as sess:
        yield sess
