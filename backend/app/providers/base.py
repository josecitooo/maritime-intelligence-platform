"""Provider contract for AIS data sources.

V1 ships a single adapter (`aisstream.io`). This protocol exists so a second
source can be added later **without touching the pipeline**.

AISHub is the obvious candidate but it is *not* a free public API: access
requires operating a physical AIS receiver and streaming a raw NMEA feed over
UDP, with coverage and uptime quality gates (see `docs/ingestion.md`). Until a
credential exists, no AISHub adapter is implemented — untested code would be
dead code.

Adapters yield already-decoded samples. Units, validation, throttling and
persistence all live downstream in `app.ingestion`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class PositionSample:
    """One decoded position report, normalised but not yet validated.

    `None` means "the source did not report this value" — which is different
    from "the value is zero". Nothing here is invented downstream.
    """

    mmsi: int
    timestamp: datetime
    latitude: float
    longitude: float
    source: str
    received_at: datetime
    cog: float | None = None
    sog: float | None = None
    heading: int | None = None
    rot: int | None = None
    nav_status: int | None = None
    draught: float | None = None
    destination: str | None = None
    eta: str | None = None
    ship_name: str | None = None


@dataclass(frozen=True, slots=True)
class StaticSample:
    """Static/voyage data (AIS message types 5 and 24): identity and dimensions."""

    mmsi: int
    source: str
    received_at: datetime
    name: str | None = None
    callsign: str | None = None
    imo: int | None = None
    ship_type: int | None = None
    dim_a: int | None = None
    dim_b: int | None = None
    dim_c: int | None = None
    dim_d: int | None = None
    draught: float | None = None
    destination: str | None = None
    eta: str | None = None


Sample = PositionSample | StaticSample


@runtime_checkable
class AISProvider(Protocol):
    """A source of AIS samples.

    Implementations must reconnect internally and yield forever; callers
    should never have to manage the transport.
    """

    name: ClassVar[str]

    def samples(self) -> AsyncIterator[Sample]:
        """Yield decoded samples indefinitely, reconnecting as needed."""
        ...
