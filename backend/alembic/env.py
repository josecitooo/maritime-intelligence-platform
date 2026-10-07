"""Alembic environment.

The migration URL comes from `app.config` rather than `alembic.ini`, so the
same settings file drives local, test and production migrations.

It also has to know which tables are *not* ours: PostGIS installs its own
reference tables into `public`, and autogenerate reads them as tables the
metadata has forgotten.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from logging.config import fileConfig
from pathlib import Path
from typing import Any

from sqlalchemy import Connection, create_engine, pool, text

from alembic import context

# Allow `app` to be imported when alembic is executed from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (registers every model's metadata)
from app.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    # `disable_existing_loggers=False` because alembic is often run *inside*
    # another process — the test suite does, and any embedder would. With the
    # default, `fileConfig` walks every logger this process already created
    # and disables the ones alembic.ini does not name, which kills `app.*`
    # for the rest of the run. Measured: `app.worker` goes from enabled to
    # disabled by a single `alembic upgrade head`.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# Escape literal `%` so URL-encoded passwords survive config interpolation.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata

#: Alembic's hook signature.
IncludeObject = Callable[[Any, str, str, bool, Any], bool]

_EXTENSION_OWNED_TABLES = """
    SELECT c.relname
    FROM pg_depend d
    JOIN pg_class c ON c.oid = d.objid
    WHERE d.classid = 'pg_class'::regclass
      AND d.refclassid = 'pg_extension'::regclass
      AND d.deptype = 'e'
"""


def _extension_owned_tables(connection: Connection) -> set[str]:
    """Tables owned by an installed extension — PostGIS, TIGER, topology.

    PostGIS creates `spatial_ref_sys` and the TIGER geocoder's tables in the
    same schema as ours. Autogenerate reads them as tables this metadata no
    longer describes and emits a `DROP TABLE` for every one; running that
    migration would delete PostGIS's own reference data and break every
    `geography` column on the way. They are not ours to drop, so they are not
    ours to compare.
    """
    return set(connection.execute(text(_EXTENSION_OWNED_TABLES)).scalars())


def _include_object(connection: Connection) -> IncludeObject:
    """Build the `include_object` hook for one connection.

    The extension query is lazy and cached: Alembic only consults this hook
    while autogenerating, and running it eagerly would open a transaction on
    `connection` before `context.begin_transaction()`. Alembic would then find
    a transaction it does not own, skip committing it, and `close()` would
    roll the whole migration back — an `upgrade head` that logs success,
    exits 0 and creates nothing.
    """

    extension_tables: set[str] | None = None

    def include_object(
        obj: Any, name: str, type_: str, reflected: bool, compare_to: Any
    ) -> bool:
        nonlocal extension_tables
        if not reflected:
            # Anything we are about to create comes from our own metadata and
            # is by definition not extension-owned.
            return True
        if type_ not in {"table", "index"}:
            return True
        if extension_tables is None:
            extension_tables = _extension_owned_tables(connection)
        if type_ == "table":
            return name not in extension_tables
        return obj.table.name not in extension_tables

    return include_object


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live database connection."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    # `engine_from_config` expects a dict of `sqlalchemy.*` keys, not the URL
    # itself: given a string it iterates the characters, finds nothing
    # prefixed, and dies on `options.pop("url")`. The URL is already in hand.
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=_include_object(connection),
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
