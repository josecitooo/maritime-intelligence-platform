"""Alembic environment.

The migration URL comes from `app.config` rather than `alembic.ini`, so the
same settings file drives local, test and production migrations.
"""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import create_engine, pool

from alembic import context

# Allow `app` to be imported when alembic is executed from backend/.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import app.models  # noqa: F401  (registers every model's metadata)
from app.config import get_settings
from app.db.base import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Escape literal `%` so URL-encoded passwords survive config interpolation.
config.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))

target_metadata = Base.metadata


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
    # itself: handed a string it iterates the characters, matches no prefixed
    # key, and dies on `options.pop("url")`. The URL is already in hand.
    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
