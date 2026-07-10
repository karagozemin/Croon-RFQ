"""SQLite engine + session helpers (via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlmodel import Session, SQLModel, create_engine

from croon.config import get_settings

_settings = get_settings()

# check_same_thread=False so FastAPI + the scheduler thread can share the engine.
engine = create_engine(
    _settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


def init_db() -> None:
    """Create tables if they don't exist. Import models first for registration."""
    import croon.models  # noqa: F401  (ensures tables are registered)

    SQLModel.metadata.create_all(engine)


def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    with Session(engine) as session:
        yield session
