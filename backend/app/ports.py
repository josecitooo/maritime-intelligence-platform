"""Port catalog and the congestion derived from it.

The two consumers of the catalog — the `/ports` router and the browser — read
the same rows through this module. Congestion is computed at read time with
PostGIS, in two steps with a precise meaning:

1. **newest-in-radius**: for every vessel, the newest stored position inside
   `radius_m` of the port (`ST_DWithin`, served by the GiST index) and fresher
   than `since`;
2. **still current**: drop that row when a *newer* position of the same
   vessel exists in the window (`NOT EXISTS`, probed through the primary key).

The order of the steps is what decides: filtering by distance *before*
picking the latest would count a vessel that sailed away but left an older
fix inside the radius. A vessel is "in the port" only if its current position
is in the port.

The split between *waiting* (anchored, moored, or moving at less than
0.5 kn) and *moving* then falls out of the surviving rows. It is deliberately
**not** stored: a `port_activity` snapshot would age exactly as it is being
read, and this API has no cache by design (`docs/architecture.md` §11, §15).
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geography
from sqlalchemy import and_, cast, func, or_, select, true
from sqlalchemy.dialects.postgresql import distinct_on

from app.models import Port, VesselPosition

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

#: How many vessels the detail view lists (recent first) beyond the counts.
_VESSELS_LIMIT = 200

#: The newest stored position of every vessel, in radius and fresher than `since`.
_LATEST_COLUMNS = (
    VesselPosition.mmsi,
    VesselPosition.sog,
    VesselPosition.nav_status,
    VesselPosition.ship_name,
    VesselPosition.timestamp,
    VesselPosition.geom,
)


class UnknownPortError(ValueError):
    """A congestion request named a port that is not in the catalog."""


def _waiting(column_sog, column_nav_status):
    """A vessel is *waiting* if stopped, anchored or moored — the row-level
    verdict the counts and the filtered views share."""
    return or_(
        and_(column_sog.is_not(None), column_sog < 0.5),
        column_nav_status.in_((1, 5)),  # anchored, moored (AIS codes)
    )


def _latest_near(georef, radius_m: float, since: datetime):
    """The derived table both queries start from: `DISTINCT ON` over the rows
    the GiST index hands back inside the radius.

    `georef` is the geometry of the port under test — either the correlated
    `ports.geom` (summary lateral) or a typed literal built from one port's
    floats (detail), which is what keeps the aggregate from scanning the whole
    window once per port.
    """
    return (
        select(*_LATEST_COLUMNS)
        .where(
            VesselPosition.timestamp >= since,
            func.ST_DWithin(VesselPosition.geom, georef, radius_m),
        )
        .order_by(VesselPosition.mmsi, VesselPosition.timestamp.desc())
        .ext(distinct_on(VesselPosition.mmsi))
        # `ports` is never a FROM here: in the summary the geometry comes from
        # the outer row (correlation), in the detail it is a bound literal.
        # Without this, SQLAlchemy would cross-join it and inflate every count
        # by the size of the catalog.
        .correlate(Port)
        .subquery("latest")
    )


def _still_current(latest, since: datetime):
    """`NOT EXISTS` a newer position of the same vessel inside the window.

    An in-radius fix only counts while nothing newer exists: the fix is the
    vessel's *current* position then, not a leftover from a departed call.
    """
    newer = VesselPosition.__table__.alias("newer")
    return ~(
        select(1)
        .select_from(newer)
        .where(
            newer.c.mmsi == latest.c.mmsi,
            newer.c.timestamp > latest.c.timestamp,
            newer.c.timestamp >= since,
        )
        .exists()
    )


def _point_near(port: Port, radius_m: float, since: datetime):
    """The detail shape, bound to one port's own floats as a typed geography:
    never the parsed geometry — the two decimals the catalog shipped."""
    georef = cast(
        func.ST_SetSRID(
            func.ST_MakePoint(port.longitude, port.latitude), 4326
        ),
        Geography("POINT", srid=4326),
    )
    return _latest_near(georef, radius_m, since)


def congestion_summaries(
    session: Session, radius_m: float, since: datetime
) -> list[tuple[Port, int, int, datetime | None]]:
    """Every port with (total, waiting, sampled_at) in one query.

    The lateral joins each port to the aggregate of its own `latest` slice.
    Counting `latest.mmsi` rather than `*` keeps a port no vessel matches at
    zero instead of one; the LEFT JOIN keeps every port in the answer.
    `moving` is derived by the caller as `total - waiting`.
    """
    latest = _latest_near(Port.geom, radius_m, since)
    per_port = (
        select(
            func.count(latest.c.mmsi).label("total"),
            func.count(latest.c.mmsi)
            .filter(_waiting(latest.c.sog, latest.c.nav_status))
            .label("waiting"),
            func.max(latest.c.timestamp).label("sampled_at"),
        )
        .select_from(latest)
        .where(_still_current(latest, since))
        .lateral("per_port")
    )
    rows = session.execute(
        select(Port, per_port.c.total, per_port.c.waiting, per_port.c.sampled_at)
        .outerjoin(per_port, true())
        .order_by(Port.name, Port.country)
    ).all()
    return [(row.Port, row.total, row.waiting, row.sampled_at) for row in rows]


def congestion_detail(
    session: Session,
    port_id: int,
    radius_m: float,
    since: datetime,
) -> tuple[Port, int, int, list[dict]]:
    """The detail for one port: counts plus the counted vessels, recent first.

    Raises `UnknownPortError` when no catalogued port has that id. The counts
    and the (capped) vessel list filter the same latest-per-vessel set against
    the port's geometry, so the list can never contradict the numbers.
    """
    port = session.get(Port, port_id)
    if port is None:
        raise UnknownPortError(port_id)

    def _counts() -> tuple[int, int, datetime | None]:
        latest = _point_near(port, radius_m, since)
        row = session.execute(
            select(
                func.count(latest.c.mmsi).label("total"),
                func.count(latest.c.mmsi)
                .filter(_waiting(latest.c.sog, latest.c.nav_status))
                .label("waiting"),
                func.max(latest.c.timestamp).label("sampled_at"),
            )
            .select_from(latest)
            .where(_still_current(latest, since))
        ).one()
        return row.total, row.waiting, row.sampled_at

    def _vessels() -> list[dict]:
        latest = _point_near(port, radius_m, since)
        rows = session.execute(
            select(latest)
            .where(_still_current(latest, since))
            .order_by(latest.c.timestamp.desc())
            .limit(_VESSELS_LIMIT)
        ).mappings()
        return [dict(row) for row in rows]

    total, waiting, _ = _counts()
    return port, total, waiting, _vessels()