"""Engine and session management.

The engine is created lazily and cached so that tests can set environment
variables before first use, and so that importing this module never opens
a connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def build_engine(url: str) -> Engine:
    """Build an engine, tuning the pool only for PostgreSQL backends."""
    kwargs: dict[str, Any] = {"future": True, "pool_pre_ping": True}
    if url.startswith("postgresql"):
        kwargs.update(pool_size=5, max_overflow=10, pool_recycle=1800)
    return create_engine(url, **kwargs)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    return build_engine(get_settings().database_url)


@lru_cache(maxsize=1)
def get_sessionmaker() -> sessionmaker[Session]:
    return sessionmaker(bind=get_engine(), expire_on_commit=False, class_=Session)


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, rollback on error, always close."""
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def ping_database() -> bool:
    """Cheap connectivity probe used by the health endpoint."""
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
    except Exception:
        return False
    return True


def reset_engine() -> None:
    """Dispose and forget the cached engine (used by tests when the URL changes)."""
    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_engine.cache_clear()
    get_sessionmaker.cache_clear()
