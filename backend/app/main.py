"""FastAPI application entrypoint.

Only `/health` exists in FASE 1 so that Docker Compose and CI have something
real to boot. Route modules land under `app/api/routers/` in FASE 6.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Response
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.config import get_settings
from app.db.session import ping_database
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
    allow_methods=["GET"],
    allow_headers=["X-API-Key"],
)


@app.get("/health", tags=["system"], summary="Application and database liveness")
def health(response: Response) -> dict[str, Any]:
    """Liveness probe.

    `last_ingestion` and `data_freshness_minutes` are deliberately absent
    until `ingestion_runs` exists (FASE 4) — they must be measured, not faked.
    """
    database_ok = ping_database()
    if not database_ok:
        response.status_code = 503

    return {
        "status": "healthy" if database_ok else "degraded",
        "database": "connected" if database_ok else "disconnected",
        "version": __version__,
        "environment": settings.environment,
        "bbox": list(settings.bbox),
        "ingestion_interval_minutes": settings.ingestion_interval_minutes,
    }
