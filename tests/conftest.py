"""Shared pytest fixtures.

Brokerage idempotency is now DURABLE (anchored in SQLite via the
``BrokerageOrder`` table), so the test suite needs a real schema to exist before
any brokerage code runs. We point the whole app at an isolated, throwaway SQLite
file for the duration of the session so tests never touch a developer's local
``croon.db`` and always start from a clean schema.

The database URL env var is set BEFORE any ``croon`` module is imported, because
``croon/db.py`` builds its engine from settings at import time. Keeping the
import lazy (inside the fixture) guarantees the override is picked up.
"""

from __future__ import annotations

import os
import tempfile

import pytest

# Must run at collection time, before test modules import croon.db.
_TMP_DB_FD, _TMP_DB_PATH = tempfile.mkstemp(prefix="croon_test_", suffix=".db")
os.close(_TMP_DB_FD)
os.environ["CROON_DATABASE_URL"] = f"sqlite:///{_TMP_DB_PATH}"
os.environ["CROON_CAP_MODE"] = "mock"


@pytest.fixture(scope="session", autouse=True)
def _init_test_db():
    """Create all tables in the throwaway DB, and clean the file up afterwards."""
    from croon.db import init_db

    init_db()
    yield
    try:
        os.remove(_TMP_DB_PATH)
    except OSError:
        pass
