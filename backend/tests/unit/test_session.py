"""Engine construction — the only place connection behaviour is configured."""

from __future__ import annotations

from typing import Any

import pytest

from app.db.session import CONNECT_TIMEOUT_SECONDS, build_engine


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """Record what `build_engine` would pass to `create_engine`."""
    captured: dict[str, Any] = {}

    def fake_create_engine(_url: str, **kwargs: Any) -> object:
        captured.update(kwargs)
        return object()

    monkeypatch.setattr("app.db.session.create_engine", fake_create_engine)
    return captured


def test_postgres_bounds_the_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    """A database that never accepts the connection must fail on our clock.

    `GET /health` is the Dockerfile healthcheck and has `--timeout=5s`. With no
    connect bound it inherits the OS TCP timeout instead: a paused or
    firewalled Supabase turns every probe into a hang, so Docker reports
    "exceeded timeout" rather than the `503 degraded` the endpoint is built to
    return — and every other caller is blocked for the same duration.
    """
    captured = _capture(monkeypatch)

    build_engine("postgresql+psycopg://user:pass@host/db")

    assert captured["connect_args"]["connect_timeout"] == CONNECT_TIMEOUT_SECONDS


def test_postgres_never_prepares_statements(monkeypatch: pytest.MonkeyPatch) -> None:
    """Supabase's session pooler cannot carry a named prepared statement.

    psycopg promotes a query to a prepared one after five runs. The session
    pooler is transaction-mode pgbouncer, so the next transaction lands on a
    backend that never saw that statement and answers "prepared statement
    does not exist" — repeated reads like `/health` hit it within minutes.
    `None` means never prepare; `0` would mean prepare at once.
    """
    captured = _capture(monkeypatch)

    build_engine("postgresql+psycopg://user:pass@host/db")

    assert captured["connect_args"]["prepare_threshold"] is None


def test_other_backends_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pool sizing, recycling and the connect bound are PostgreSQL-specific.

    `sqlite+pysqlite` rejects `pool_size` outright, so widening the branch
    would break rather than merely mis-tune — this asserts the condition that
    keeps the two apart.
    """
    captured = _capture(monkeypatch)

    build_engine("sqlite+pysqlite:///:memory:")

    assert "connect_args" not in captured
    assert "pool_size" not in captured
    assert "pool_recycle" not in captured
