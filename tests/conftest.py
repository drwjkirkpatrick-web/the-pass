"""Shared pytest fixtures for The Pass.

NOTE: `db` is an in-memory database migrated with the full schema, and
`config` points at a temporary photo directory. Tests never touch the
restaurant's real database or files.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import Config
from core.database import Database


@pytest.fixture
def db():
    database = Database(":memory:").migrate()
    yield database
    database.close()


@pytest.fixture
def config(tmp_path):
    return Config.default().with_overrides(
        db_path=":memory:", photo_dir=str(tmp_path / "photos")
    )


@pytest.fixture
def bus():
    # imported lazily so this fixture is usable from Prompt 03 onward
    from core.events import EventBus

    return EventBus()
