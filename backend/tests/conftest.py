"""Shared test configuration.

Environment variables must be set BEFORE the first ``get_settings()`` call,
because settings are memoised with ``lru_cache``. Values asserted on by the
config tests are forced here so a developer's local ``.env`` cannot leak in.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from app.config import Settings

os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_FORMAT"] = "json"
os.environ.pop("AISSTREAM_API_KEY", None)
# Blank, not unset: unsetting it would let the developer's `.env` decide and
# 401 every request. An empty value outranks the file, so this forces "no key".
os.environ["API_READ_KEY"] = ""
# Writes are *disabled* for the test run too: a developer key in `.env` must
# not silently open the region-selection surface during test runs. Tests that
# exercise the write path set their own key first.
os.environ["API_WRITE_KEY"] = ""
# Port 5433 matches docker-compose.test.yml. CI overrides this with its own
# DATABASE_URL (port 5432, service container) — setdefault keeps that override.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://maritime_test:maritime_test@localhost:5433/maritime_test",
)

PROBE_FIXTURE = Path(__file__).parent / "fixtures" / "probe_sample.jsonl"


@pytest.fixture(scope="session")
def probe_frames() -> list[dict[str, Any]]:
    """Recorded aisstream frames — the only real data the unit tests see.

    `scope="session"` because the file is a few hundred kilobytes and no test
    mutates it.
    """
    lines = PROBE_FIXTURE.read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines if line.strip()]


@pytest.fixture
def app_settings() -> Settings:
    """Settings that never read the developer's `.env`.

    Tests must not depend on — or expose — a real API key, so the file is
    explicitly disabled rather than merely absent from the environment.
    """
    return Settings(_env_file=None, aisstream_api_key="unit-test-key")
