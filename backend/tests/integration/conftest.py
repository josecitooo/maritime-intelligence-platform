"""Fixtures for the tests that talk to PostGIS.

Bring the database up first — ``docker compose -f docker-compose.test.yml up
-d --wait`` (README §4). Nothing here skips when it is missing: CI provisions
the same container, and a suite that quietly passes without the database is a
suite that has quietly stopped testing the write path.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import text

from alembic import command
from app.db.session import session_scope

BACKEND = Path(__file__).resolve().parents[2]

_TRUNCATE = (
    "TRUNCATE TABLE vessel_positions, vessels, ingestion_runs, "
    "export_runs RESTART IDENTITY"
)


@pytest.fixture(scope="session")
def migrated() -> None:
    """Apply the real migration once per session.

    Deliberately not `metadata.create_all()`: the migration is the artifact
    that runs against Supabase, so if it is wrong these tests are the ones
    that should say so. `alembic check` then keeps the two in step.
    """
    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(config, "head")


@pytest.fixture(autouse=True)
def empty_tables(migrated: None) -> Iterator[None]:
    """Give every test the whole database to itself."""
    with session_scope() as session:
        session.execute(text(_TRUNCATE))
    yield
    with session_scope() as session:
        session.execute(text(_TRUNCATE))
