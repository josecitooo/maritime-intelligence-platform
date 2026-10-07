"""FastAPI application entrypoint.

Route modules live under `app/api/routers/` (FASE 6); this module only wires
them up, configures CORS and runs the lifespan.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api.routers import health, positions, regions, vessels
from app.config import get_settings
from app.logging import setup_logging

settings = get_settings()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    setup_logging(settings.log_level, settings.log_format)
    logger.info(
        "application.started",
        extra={
            "environment": settings.environment,
            "version": __version__,
            "bbox": list(settings.bbox),
            "cors_origins": settings.cors_origins,
        },
    )
    yield
    logger.info("application.stopped")


app = FastAPI(
    title="Maritime Intelligence Platform",
    description=(
        "AIS ingestion, validation and near real-time maritime visualisation. "
        "Data is refreshed every configured ingestion interval, not in real time."
    ),
    version=__version__,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "PUT"],
    allow_headers=["X-API-Key", "X-Write-Key"],
)

# `/health` carries no read-key dependency: a liveness probe must work without
# a secret (`app/api/auth.py`). `GET /regions` is similarly open — it is
# operational metadata, and the browser needs it before any credential exists.
app.include_router(health.router)
app.include_router(positions.router)
app.include_router(regions.router)
app.include_router(vessels.router)
