"""`X-API-Key` enforcement — no database required.

A rejected request never reaches a handler, so every assertion here holds with
the suite pointed at a dead port: the guard is the whole subject.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from app.api.auth import require_read_key
from app.config import get_settings
from app.main import app as application

_KEY = "unit-test-key"

#: Every route the read key guards. `/health` is deliberately absent: a
#: liveness probe must answer without a secret.
_GUARDED = (
    "/positions/latest",
    "/vessels",
    "/vessels/215123456",
    "/vessels/215123456/track",
)


@pytest.fixture()
def keyed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Configure a read key for one test and rebuild the cached settings."""
    monkeypatch.setenv("API_READ_KEY", _KEY)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture()
def unkeyed(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Explicitly *no* key: the developer's `.env` must not decide this.

    Set to empty rather than deleted — deleting it would hand the decision
    back to `.env`, which is exactly what this fixture refuses.
    """
    monkeypatch.setenv("API_READ_KEY", "")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _probe() -> FastAPI:
    """A one-route app wearing the same dependency the routers wear."""
    probe = FastAPI()
    probe.add_api_route(
        "/data",
        lambda: {"ok": True},
        methods=["GET"],
        dependencies=[Depends(require_read_key)],
    )
    return probe


def test_guarded_routes_reject_a_missing_key(keyed: None) -> None:
    with TestClient(application) as client:
        for path in _GUARDED:
            response = client.get(path)
            assert response.status_code == 401, path
            assert response.headers["www-authenticate"] == "ApiKey"


def test_guarded_routes_reject_a_wrong_key(keyed: None) -> None:
    with TestClient(application) as client:
        for path in _GUARDED:
            response = client.get(path, headers={"X-API-Key": "not-the-key"})
            assert response.status_code == 401, path


def test_health_is_not_guarded(keyed: None) -> None:
    """A container healthcheck cannot carry a secret, so `/health` always answers.

    Anything other than 401 proves the dependency is not wired onto it: the
    status is 200 against the test database and 503 against a dead one.
    """
    with TestClient(application) as client:
        assert client.get("/health").status_code != 401


def test_the_matching_key_reaches_the_handler(keyed: None) -> None:
    with TestClient(_probe()) as client:
        response = client.get("/data", headers={"X-API-Key": _KEY})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_routes_are_open_when_no_key_is_configured(unkeyed: None) -> None:
    """The default is an open API — development and the demo stack depend on it."""
    with TestClient(_probe()) as client:
        assert client.get("/data").status_code == 200
