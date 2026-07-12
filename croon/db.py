"""Database engine and session management.

Supports both SQLite (default, demo-grade) and any SQLAlchemy URL such as
Postgres (recommended for a hosted deployment where the container filesystem is
ephemeral). The engine is configured per-dialect so switching persistence
backends is a pure CROON_DATABASE_URL change - no code edits.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from croon.config import get_settings


_settings = get_settings()


def _make_engine(database_url: str):
    """Build a dialect-appropriate engine.

    * SQLite - set check_same_thread=False so the FastAPI request threads and
      the background scheduler thread can share one connection, and eagerly
      create the parent directory. The second part is what makes a *persistent*
      path work: point CROON_DATABASE_URL at a mounted volume (e.g.
      sqlite:////data/croon.db) and the file survives redeploys instead of
      living in an ephemeral /tmp that DigitalOcean wipes on every rebuild.
    * Non-SQLite (e.g. Postgres) - do NOT pass check_same_thread (it's a
      SQLite-only arg and errors elsewhere); the driver's own pool handles
      cross-thread access. This is the truly durable option for a hosted app.
    """
    if database_url.startswith("sqlite"):
        # Extract the on-disk path (everything after the scheme's ':///').
        # sqlite:///relative.db  -> relative.db
        # sqlite:////data/abs.db -> /data/abs.db
        raw_path = database_url.split(":///", 1)[-1]
        if raw_path and raw_path != ":memory:":
            parent = Path(raw_path).expanduser().parent
            if str(parent) not in ("", "."):
                os.makedirs(parent, exist_ok=True)
        return create_engine(
            database_url,
            echo=False,
            connect_args={"check_same_thread": False},
        )

    # Postgres / MySQL / etc. - pre_ping avoids stale-connection errors after a
    # hosted DB idles the socket.
    return create_engine(database_url, echo=False, pool_pre_ping=True)


# check_same_thread handling + persistent-path creation live in _make_engine.
engine = _make_engine(_settings.database_url)



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
