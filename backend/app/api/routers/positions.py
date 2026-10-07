"""`GET /positions/latest` — the payload the 3D map renders.

One row per vessel: the newest position that made it past validation. This is
the API's hot path, and it is answered entirely from the primary key
`(mmsi, timestamp)` with no cache and no materialised view — see
`docs/architecture.md` §11.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import distinct_on

from app.api.auth import require_read_key
from app.db.session import session_scope
from app.models import VesselPosition
from app.schemas.positions import PositionLatest

router = APIRouter(tags=["positions"], dependencies=[Depends(require_read_key)])

#: Exactly the fields `PositionLatest` declares — a column rename shows up as a
#: validation error here instead of silently dropping out of the response.
_COLUMNS = (
    VesselPosition.mmsi,
    VesselPosition.timestamp,
    VesselPosition.latitude,
    VesselPosition.longitude,
    VesselPosition.sog,
    VesselPosition.cog,
    VesselPosition.heading,
    VesselPosition.nav_status,
    VesselPosition.ship_name,
    VesselPosition.flags,
)


@router.get("/positions/latest", response_model=list[PositionLatest])
def latest_positions(limit: int = Query(default=2000, ge=1, le=10000)) -> list[PositionLatest]:
    """The newest stored position of each vessel, freshest first.

    `limit` bounds the payload rather than the vessel count: the map wants the
    traffic that is moving now, so when a region holds more vessels than fit in
    one response, the ones last seen longest ago fall off the end. It is
    refetched only when `/health` reports a new `last_flush`
    (`docs/architecture.md` §9), so this runs once per ingestion interval and
    not once per browser poll.
    """
    newest_per_vessel = (
        select(*_COLUMNS)
        .order_by(VesselPosition.mmsi, VesselPosition.timestamp.desc())
        .ext(distinct_on(VesselPosition.mmsi))
        .subquery()
    )
    # `DISTINCT ON` rides the primary key, so "latest row per vessel" is a
    # property of the index; the outer sort is over one row per vessel.
    stmt = select(newest_per_vessel).order_by(newest_per_vessel.c.timestamp.desc()).limit(limit)

    with session_scope() as session:
        return [PositionLatest(**row) for row in session.execute(stmt).mappings()]
