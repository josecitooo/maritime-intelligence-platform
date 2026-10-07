"""Position contracts: what the map draws and what a track is made of.

`rot` and `geom` are deliberately absent. Rate of turn is not displayed
(`docs/ingestion.md` §9) and `geom` is generated from `latitude` and
`longitude` (`docs/data-model.md` §2), so sending it would only be a second
copy of coordinates the client already has.

`nav_status` is the raw AIS code, not a label: the API stores and returns what
the source reported, and 15 ("not defined") is returned as 15 rather than
nulled, so a viewer can tell *unknown* apart from *not reported*.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class Position(BaseModel):
    """One stored position, without identity — the vessel is in the request path."""

    timestamp: datetime
    latitude: float
    longitude: float
    sog: float | None = Field(
        description="Speed over ground in knots; null when AIS did not report it."
    )
    cog: float | None = Field(
        description="Course over ground in degrees; 360 (not available) arrives as null."
    )
    heading: int | None = Field(
        description="True heading in degrees; 511 (not available) arrives as null."
    )
    nav_status: int | None = Field(
        description="AIS navigational status code; 15 means not defined and is kept as 15."
    )
    ship_name: str | None
    flags: list[str] = Field(
        description="Empty when the row is believed; `sog_implausible` / `position_jump` otherwise."
    )


class PositionLatest(Position):
    """The newest position of one vessel, as the map keys it.

    `ship_type` rides along from `vessels` rather than the position row:
    identity and positions are deliberately unrelated tables
    (`docs/architecture.md` §16), so the AIS type code is null whenever AIS
    reported a position but no static data for that MMSI.
    """

    mmsi: int
    ship_type: int | None = Field(
        description="AIS ship type code, joined from the identity table when it exists."
    )
