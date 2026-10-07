"""`GET /health` — the response contract.

Two ages travel with liveness on purpose. `last_flush` says whether the worker
is still committing windows; `last_ais_message` says whether AIS is still
producing anything worth committing. A pipeline flushing empty windows on
schedule is alive with no new data, and a pipeline that stopped has a newest
message it will never advance — one number cannot say both. The client decides
what to do about it (`docs/architecture.md` §11).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Liveness plus the freshness a client needs to avoid showing stale data."""

    status: Literal["healthy", "degraded"]
    database: Literal["connected", "disconnected"]
    version: str
    environment: str
    bbox: list[float]
    ingestion_interval_minutes: int
    last_flush: datetime | None = Field(
        description="When the worker last committed a window; null before the first flush."
    )
    last_ais_message: datetime | None = Field(
        description=(
            "Timestamp of the newest AIS message stored; null when nothing has been "
            "stored yet. Read through the index on `timestamp`."
        )
    )
    data_freshness_minutes: float | None = Field(
        description=(
            "Age of `last_ais_message`, in minutes, clamped at zero because a "
            "transponder clock can run ahead of ours. Null when there is no data."
        )
    )
