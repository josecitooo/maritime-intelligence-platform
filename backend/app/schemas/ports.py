"""`/ports` — the port catalog and the congestion derived from it.

Both responses embed the full port row (`id`, `ne_id`, name, country,
coordinates) so the map can draw a port and its reading from one payload; a
separate bare-catalog endpoint would be second thing the browser has to join.
Congestion is a *derivation over `vessel_positions`*, not a stored value: the
counts answer "what is the newest stored position of every vessel inside the
radius and fresher than `since`" (`docs/architecture.md` §15).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class PortResponse(BaseModel):
    """One catalogued port, as served to the map."""

    id: int
    ne_id: int = Field(
        description="The record's id in the Natural Earth 1:10m ports layer — provenance."
    )
    name: str
    country: str
    latitude: float
    longitude: float


class PortVessel(BaseModel):
    """One vessel counted in a port's congestion."""

    mmsi: int
    ship_name: str | None
    sog: float | None
    nav_status: int | None
    timestamp: datetime


class PortCongestionSummary(BaseModel):
    """One port and how many vessels its reading counts, for the map layer."""

    port: PortResponse
    radius_km: int
    #: Cutoff freshness: positions older than this are no longer "in the port".
    since: datetime
    total: int
    waiting: int
    moving: int
    #: The newest counted position — the freshest evidence behind the counts.
    sampled_at: datetime | None


class PortCongestion(PortCongestionSummary):
    """The detail view: counts plus the vessels that produced them."""

    vessels: list[PortVessel] = Field(
        description="The vessels counted, most recently reported first; capped at 200."
    )