"""SQLite engine + session helpers (via SQLModel)."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from croon.config import get_settings


_settings = get_settings()

# check_same_thread=False so FastAPI + the scheduler thread can share the engine.
engine = create_engine(
    _settings.database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


# Additive, idempotent column migrations for existing SQLite DBs.
# SQLModel.create_all() creates MISSING tables but never ALTERs an existing one,
# so a column added to a model after a DB already exists would silently be
# absent. We keep this list tiny and additive-only (SQLite ALTER TABLE ADD
# COLUMN is safe and non-locking); anything more complex would warrant Alembic.
_COLUMN_MIGRATIONS: dict[str, dict[str, str]] = {
    # table -> {column: "TYPE DEFAULT ..."}
    "run": {"mode": "VARCHAR DEFAULT 'mock'"},
}


def _apply_additive_migrations() -> None:
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in _COLUMN_MIGRATIONS.items():
            if table not in existing_tables:
                continue  # create_all will build it fresh with all columns
            present = {c["name"] for c in inspector.get_columns(table)}
            for col, ddl in columns.items():
                if col not in present:
                    conn.execute(
                        text(f'ALTER TABLE "{table}" ADD COLUMN {col} {ddl}')
                    )


def init_db() -> None:
    """Create tables if they don't exist. Import models first for registration."""
    import croon.models  # noqa: F401  (ensures tables are registered)

    SQLModel.metadata.create_all(engine)
    _apply_additive_migrations()



def get_session() -> Iterator[Session]:
    """FastAPI dependency: yields a session, closes it after the request."""
    with Session(engine) as session:
        yield session
