"""Vessel contracts: identity, detail, and identity joined to a position.

There is deliberately no foreign key between `vessels` and `vessel_positions`
(`docs/data-model.md` §2), so these schemas must not pretend the two line up
either: `VesselDetail` reports `updated_at` as null when AIS has never sent
static data for an MMSI, rather than inventing a placeholder row.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.positions import Position


class VesselSummary(BaseModel):
    """Static identity for one vessel — a row of `vessels`.

    Only vessels that have reported static data appear in list responses;
    the rest are reachable through `/positions/latest`, where `ship_name`
    carries the name when the position message has one.
    """

    mmsi: int
    name: str | None
    callsign: str | None
    ship_type: int | None = Field(
        description="AIS ship-type code; the client maps it to a label."
    )
    length_m: int | None
    width_m: int | None
    updated_at: datetime = Field(
        description="When AIS last refreshed this identity (never moves backwards)."
    )


class VesselDetail(BaseModel):
    """Identity plus the latest position for one MMSI.

    A vessel can exist on the map without a `vessels` row — identity coverage
    of positioned vessels is 52.7 % (`docs/ingestion.md` §2) — so every static
    field is nullable and `updated_at` is null exactly when no static data has
    ever been reported.
    """

    mmsi: int
    name: str | None
    callsign: str | None
    imo: int | None
    ship_type: int | None = Field(
        description="AIS ship-type code; the client maps it to a label."
    )
    length_m: int | None
    width_m: int | None
    draught_m: float | None = Field(description="Static draught in metres.")
    destination: str | None = Field(
        description="AIS `DEST`; blank at the source decodes to null and is often empty."
    )
    eta: str | None = Field(
        description=(
            "Exactly as AIS reports it: `MM-DD HH:MM`, with **no year**. "
            "Promoting it to a date would invent one (`docs/ingestion.md` §9)."
        )
    )
    updated_at: datetime | None = Field(
        description="Null when this MMSI has never reported static data."
    )
    last_position: Position | None = Field(
        description="Newest stored position; null when the vessel has only static data."
    )
