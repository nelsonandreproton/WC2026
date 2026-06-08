"""Shared pytest fixtures.

DB fixture follows the project's mandated teardown order:
session.close() -> engine.dispose() -> os.unlink().
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest
from sqlalchemy.orm import Session

from wc2026bot.db.session import init_db, make_engine, make_session_factory


@pytest.fixture
def db_session() -> Iterator[Session]:
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name

    engine = make_engine(db_path)
    init_db(engine)
    factory = make_session_factory(engine)
    session = factory()

    try:
        yield session
    finally:
        session.close()
        engine.dispose()
        os.unlink(db_path)
