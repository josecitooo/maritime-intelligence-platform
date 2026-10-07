"""`/ports` — the port catalog and the congestion derived from it.

Congestion answers are data (they count vessels from `vessel_positions`), so
both routes sit behind the read key like `/positions` does — unlike `GET
/regions`, which stays open because it says nothing about vessels. Congestion
is computed at read time over PostGIS (`app/ports.py`); there is no
`port_activity` table to keep fresh.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status

from app.api.auth import require_read_key
from app.config import get_settings
from app.db.session import session_scope
from app.ports import UnknownPortError, congestion_detail, congestion_summaries
from app.schemas.ports import (
    PortCongestion,
    PortCongestionSummary,
    PortResponse,
    PortVessel,
)

router = APIRouter(tags=["ports"], dependencies=[Depends(require_read_key)])

#: The detail view caps the vessel list, not the counts (see the schema).
_MAX_VESSELS = 200


def _port_response(port) -> PortResponse:
    return PortResponse(
        id=port.id,
        ne_id=port.ne_id,
        name=port.name,
        country=port.country,
        latitude=port.latitude,
        longitude=port.longitude,
    )


def _window(
    radius_km: int | None, recency_hours: int | None
) -> tuple[int, datetime]:
    """Resolve the query params against the configured defaults."""
    settings = get_settings()
    radius = radius_km or settings.port_congestion_radius_km
    recency = recency_hours or settings.port_congestion_recency_hours
    since = datetime.now(UTC) - timedelta(hours=recency)
    return radius, since


@router.get("/ports/congestion", response_model=list[PortCongestionSummary])
def list_congestion(
    radius_km: int = Query(default=None, ge=1, le=500),
    recency_hours: int = Query(default=None, ge=1, le=168),
) -> list[PortCongestionSummary]:
    """Every port with the counts its reading derives, for the map layer.

    One LATERAL query against `vessel_positions` — the whole layer arrives in
    a single payload, so the browser never joins a bare catalog by hand.
    """
    radius, since = _window(radius_km, recency_hours)
    with session_scope() as session:
        rows = congestion_summaries(session, radius * 1000, since)
        return [
            PortCongestionSummary(
                port=_port_response(port),
                radius_km=radius,
                since=since,
                total=total,
                waiting=waiting,
                moving=total - waiting,
                sampled_at=sampled_at,
            )
            for port, total, waiting, sampled_at in rows
        ]


@router.get("/ports/{port_id}/congestion", response_model=PortCongestion)
def port_congestion(
    port_id: int = Path(ge=1),
    radius_km: int = Query(default=None, ge=1, le=500),
    recency_hours: int = Query(default=None, ge=1, le=168),
) -> PortCongestion:
    """The detail for one port: counts plus the counted vessels, freshest first."""
    radius, since = _window(radius_km, recency_hours)
    with session_scope() as session:
        try:
            port, total, waiting, vessels = congestion_detail(
                session, port_id, radius * 1000, since
            )
        except UnknownPortError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No catalogued port with id {port_id}",
            ) from None
        return PortCongestion(
            port=_port_response(port),
            radius_km=radius,
            since=since,
            total=total,
            waiting=waiting,
            moving=total - waiting,
            sampled_at=vessels[0]["timestamp"] if vessels else None,
            vessels=[
                PortVessel(
                    mmsi=row["mmsi"],
                    ship_name=row["ship_name"],
                    sog=row["sog"],
                    nav_status=row["nav_status"],
                    timestamp=row["timestamp"],
                )
                for row in vessels
            ],
        )