"""`/vessels` — identity, detail and track.

Identity and positions are deliberately unrelated tables (`docs/data-model.md`
§2), so nothing here assumes they line up: a vessel can be on the map with no
static data at all, and a static row can exist with no stored position.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import select

from app.api.auth import require_read_key
from app.db.session import session_scope
from app.models import Vessel, VesselPosition
from app.schemas.positions import Position
from app.schemas.vessels import VesselDetail, VesselSummary

router = APIRouter(tags=["vessels"], dependencies=[Depends(require_read_key)])

_DESCRIPTION = "Nine-digit AIS MMSI — the pipeline rejects anything else at ingest."


@router.get("/vessels", response_model=list[VesselSummary])
def list_vessels(limit: int = Query(default=1000, ge=1, le=5000)) -> list[VesselSummary]:
    """Static identity, most recently refreshed first.

    Only vessels that have reported static data are listed: identity coverage
    of positioned vessels is 52.7 % (`docs/ingestion.md` §2), so the other
    half has no row to return. Those vessels still appear on the map through
    `/positions/latest`, where `ship_name` carries the name when the position
    message includes one — this endpoint is a directory of what AIS told us,
    not a census of what is moving.
    """
    with session_scope() as session:
        vessels = session.scalars(
            select(Vessel).order_by(Vessel.updated_at.desc()).limit(limit)
        ).all()
        return [
            VesselSummary(
                mmsi=vessel.mmsi,
                name=vessel.name,
                callsign=vessel.callsign,
                ship_type=vessel.ship_type,
                length_m=vessel.length_m,
                width_m=vessel.width_m,
                updated_at=vessel.updated_at,
            )
            for vessel in vessels
        ]


@router.get("/vessels/{mmsi}", response_model=VesselDetail)
def get_vessel(mmsi: int = Path(ge=100_000_000, le=999_999_999, description=_DESCRIPTION)) -> VesselDetail:
    """One MMSI: what AIS told us about it, plus where it was last seen.

    404 only when neither table knows the MMSI. With no static data the
    identity fields and `updated_at` are null, which is the honest shape for
    the roughly half of the vessels that have never sent a type 5 or 24
    message inside the region.
    """
    with session_scope() as session:
        vessel = session.get(Vessel, mmsi)
        position = session.scalar(
            select(VesselPosition)
            .where(VesselPosition.mmsi == mmsi)
            .order_by(VesselPosition.timestamp.desc())
            .limit(1)
        )
        if vessel is None and position is None:
            raise HTTPException(status_code=404, detail=f"Nothing stored for MMSI {mmsi}")

        return VesselDetail(
            mmsi=mmsi,
            name=vessel.name if vessel else None,
            callsign=vessel.callsign if vessel else None,
            imo=vessel.imo if vessel else None,
            ship_type=vessel.ship_type if vessel else None,
            length_m=vessel.length_m if vessel else None,
            width_m=vessel.width_m if vessel else None,
            draught_m=vessel.draught_m if vessel else None,
            destination=vessel.destination if vessel else None,
            eta=vessel.eta if vessel else None,
            updated_at=vessel.updated_at if vessel else None,
            last_position=_to_position(position) if position else None,
        )


@router.get("/vessels/{mmsi}/track", response_model=list[Position])
def get_track(
    mmsi: int = Path(ge=100_000_000, le=999_999_999, description=_DESCRIPTION),
    limit: int = Query(default=5000, ge=1, le=20000),
) -> list[Position]:
    """Every stored position for one vessel, oldest first — the line the map draws.

    Ordered by `timestamp` ascending so the client builds the polyline
    directly; the primary key `(mmsi, timestamp)` already holds it in that
    order, so this is an index read rather than a sort. The default bound is
    far above what retention can produce for a single vessel (7 days at
    `POSITION_INTERVAL_MINUTES` = 10 is 1 008 rows), so it is a guard against
    a mis-set configuration rather than a pagination control.

    An MMSI with nothing stored returns an empty list, not 404: with no foreign
    key between identity and positions, "does this vessel exist" is not a
    question the schema can answer, and an empty track is the honest reply.
    """
    with session_scope() as session:
        rows = session.scalars(
            select(VesselPosition)
            .where(VesselPosition.mmsi == mmsi)
            .order_by(VesselPosition.timestamp)
            .limit(limit)
        ).all()
        return [_to_position(row) for row in rows]


def _to_position(row: VesselPosition) -> Position:
    """Map a stored row onto the public position contract.

    Written out field by field on purpose: renaming a column leaves the ORM
    attribute behind, so the mismatch surfaces here as an `AttributeError`
    instead of quietly emptying a field of the response.
    """
    return Position(
        timestamp=row.timestamp,
        latitude=row.latitude,
        longitude=row.longitude,
        sog=row.sog,
        cog=row.cog,
        heading=row.heading,
        nav_status=row.nav_status,
        ship_name=row.ship_name,
        flags=row.flags,
    )
