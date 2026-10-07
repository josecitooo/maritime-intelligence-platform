"""`GET /health` — liveness, database state, and how old the data is."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Response
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db.session import session_scope
from app.models import IngestionRun, VesselPosition
from app.schemas.health import HealthResponse

logger = logging.getLogger(__name__)

router = APIRouter(tags=["system"])

#: One round trip, both aggregates index-backed: `window_end` is read from a
#: table of ~48 rows a day, and `max(timestamp)` stops at the first entry of
#: `ix_vessel_positions_timestamp`. Nothing here scans `vessel_positions`, so
#: the endpoint stays cheap enough for a browser to poll (`docs/architecture.md` §9).
_FRESHNESS = select(
    select(func.max(IngestionRun.window_end)).scalar_subquery(),
    select(func.max(VesselPosition.timestamp)).scalar_subquery(),
)


@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Application, database and data freshness",
)
def health(response: Response) -> HealthResponse:
    """Liveness probe carrying the two ages that make a stall diagnosable.

    `last_flush` and `last_ais_message` answer different questions: a worker
    flushing empty windows on schedule is alive but has no new data, and a
    worker that stopped has a newest message it will never advance. Reporting
    only one of them would let either failure look healthy.
    """
    settings = get_settings()
    last_flush: datetime | None = None
    last_ais_message: datetime | None = None
    database_ok = True

    try:
        with session_scope() as session:
            last_flush, last_ais_message = session.execute(_FRESHNESS).one()
    except Exception as exc:
        database_ok = False
        response.status_code = 503
        logger.warning("health.database_unavailable", extra={"error": str(exc)})

    return HealthResponse(
        status="healthy" if database_ok else "degraded",
        database="connected" if database_ok else "disconnected",
        version=__version__,
        environment=settings.environment,
        bbox=list(settings.bbox),
        ingestion_interval_minutes=settings.ingestion_interval_minutes,
        last_flush=last_flush,
        last_ais_message=last_ais_message,
        data_freshness_minutes=_freshness_minutes(last_ais_message),
    )


def _freshness_minutes(last_ais_message: datetime | None) -> float | None:
    """Age of the newest stored message, in minutes; null when there is none.

    Clamped at zero: the timestamp comes from the transponder, whose clock can
    run ahead of ours, and a negative age would read as a bug rather than as
    clock skew.
    """
    if last_ais_message is None:
        return None
    age = (datetime.now(UTC) - last_ais_message).total_seconds() / 60.0
    return round(max(age, 0.0), 1)
