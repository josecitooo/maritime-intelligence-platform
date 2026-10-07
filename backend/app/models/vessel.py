"""Vessel identity and the position track — the two tables FASE 4 writes.

There is deliberately **no foreign key** from `vessel_positions.mmsi` to
`vessels.mmsi`. Static data is not guaranteed: the type-24 subscription raised
identity coverage of positioned vessels from 24.7 % to 52.7 %
(`docs/ingestion.md` §2), so roughly half the vessels on the map have no
`vessels` row at all. A foreign key would reject exactly the positions the
product exists to draw. A position identifies itself; identity is a bonus.
"""

from __future__ import annotations

from datetime import datetime

from geoalchemy2 import Geography
from sqlalchemy import Computed, DateTime, Float, Index, Integer, SmallInteger, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: The geometry is *derived*, never written: the decoder's two floats are the
#: only source of truth, so `geom` cannot drift from the row it belongs to and
#: the persistence layer never has to know PostGIS exists. Postgres accepts
#: this because every function in it is immutable.
_GEOM_EXPRESSION = "ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography"


class Vessel(Base):
    """Static identity, as last reported by an AIS type 5 or 24 message.

    One row per MMSI, merged null-preserving so a message that carries only a
    name (type 24 part A) cannot erase a known ship type (see the upsert in
    `app.ingestion.persistence`).

    `eta` is stored exactly as AIS reports it — `MM-DD HH:MM`, with **no
    year**. Parsing it into a timestamp would invent one; `docs/ingestion.md`
    §9 records the limitation.
    """

    __tablename__ = "vessels"

    #: Identity assigned by the AIS source, never by a sequence — hence
    #: ``autoincrement=False``, which stops SQLAlchemy reading it as a
    #: surrogate key and rendering the column ``SERIAL``.
    mmsi: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    name: Mapped[str | None] = mapped_column(Text)
    callsign: Mapped[str | None] = mapped_column(Text)
    imo: Mapped[int | None] = mapped_column(Integer)
    ship_type: Mapped[int | None] = mapped_column(SmallInteger)
    length_m: Mapped[int | None] = mapped_column(SmallInteger)
    width_m: Mapped[int | None] = mapped_column(SmallInteger)
    draught_m: Mapped[float | None] = mapped_column(Float)
    destination: Mapped[str | None] = mapped_column(Text)
    eta: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class VesselPosition(Base):
    """One throttled position report.

    Primary key `(mmsi, timestamp)` makes a replayed window a no-op under
    `ON CONFLICT DO NOTHING` (`docs/ingestion.md` §6), and gives "this
    vessel's track" an index for free — the key is already in track order.

    `(timestamp)` is indexed separately: retention deletes by message time
    across all vessels, which the primary key cannot serve.

    `flags` is the row-level verdict — `sog_implausible`, `position_jump`, or
    neither. The per-window roll-up lives in `ingestion_runs.flagged`; this
    column is what lets a viewer ask *which* of the 3 000 positions in a
    window nobody should believe.
    """

    __tablename__ = "vessel_positions"

    mmsi: Mapped[int] = mapped_column(Integer, primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    sog: Mapped[float | None] = mapped_column(Float)
    cog: Mapped[float | None] = mapped_column(Float)
    heading: Mapped[int | None] = mapped_column(SmallInteger)
    rot: Mapped[int | None] = mapped_column(SmallInteger)
    nav_status: Mapped[int | None] = mapped_column(SmallInteger)
    ship_name: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: Stored as a sorted list so a replayed window writes byte-identical
    #: rows; read back as one. Written only by `app.ingestion.persistence`.
    flags: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False)
    geom: Mapped[object] = mapped_column(
        Geography("Point", srid=4326, spatial_index=True),
        # `persisted=True` is not cosmetic: without it SQLAlchemy renders no
        # keyword at all on PostgreSQL 18+, where VIRTUAL becomes the default —
        # and a virtual generated column cannot carry the GiST index below.
        Computed(_GEOM_EXPRESSION, persisted=True),
        nullable=False,
    )

    __table_args__ = (
        Index("ix_vessel_positions_timestamp", "timestamp"),
    )
