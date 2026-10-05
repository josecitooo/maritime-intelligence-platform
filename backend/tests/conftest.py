"""Shared test configuration.

Environment variables must be set BEFORE the first ``get_settings()`` call,
because settings are memoised with ``lru_cache``. Values asserted on by the
config tests are forced here so a developer's local ``.env`` cannot leak in.
"""

from __future__ import annotations

import os

os.environ["ENVIRONMENT"] = "test"
os.environ["LOG_FORMAT"] = "json"
os.environ.pop("AISSTREAM_API_KEY", None)
# Port 5433 matches docker-compose.test.yml. CI overrides this with its own
# DATABASE_URL (port 5432, service container) — setdefault keeps that override.
os.environ.setdefault(
    "DATABASE_URL",
    "postgresql+psycopg://maritime_test:maritime_test@localhost:5433/maritime_test",
)
