"""The port catalog — `ports` (FASE 10).

One row per real-world port, seeded from the Natural Earth 1:10m ports layer
(public domain, naturalearthdata.com) — the same source family as the
frontend's coastline. `ne_id` is that dataset's id for the record, so any row
can be cross-checked against the source instead of being trusted on faith,
and the coordinates are the dataset's, never hand-typed.

`geom` is derived from `latitude`/`longitude` exactly like
`vessel_positions.geom`: the two floats are the only source of truth, the
generated column keeps the point in step with its row, and the GiST index
makes "vessels within N km of this port" (`ST_DWithin`) a proximity query
instead of a scan (`docs/architecture.md` §7).
"""

from __future__ import annotations

from geoalchemy2 import Geography
from sqlalchemy import BigInteger, Computed, Float, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

#: The same derived geometry rule `vessel_positions` uses, written again so
#: the two tables stay independently readable; Postgres accepts both because
#: every function involved is immutable.
_GEOM_EXPRESSION = "ST_SetSRID(ST_MakePoint(longitude, latitude), 4326)::geography"


class Port(Base):
    __tablename__ = "ports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    #: The record's id in the Natural Earth 1:10m ports layer — the verifiable
    #: provenance of this row's coordinates, not an operational key.
    ne_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    country: Mapped[str] = mapped_column(Text, nullable=False)
    latitude: Mapped[float] = mapped_column(Float, nullable=False)
    longitude: Mapped[float] = mapped_column(Float, nullable=False)
    geom: Mapped[object] = mapped_column(
        Geography("Point", srid=4326, spatial_index=True),
        Computed(_GEOM_EXPRESSION, persisted=True),
        nullable=False,
    )