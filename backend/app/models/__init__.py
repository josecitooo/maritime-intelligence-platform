"""ORM models.

Alembic imports this package so every model registers its metadata before
autogenerate runs. The tables arrive with the phase that consumes them rather
than as empty scaffolding: `vessels`, `vessel_positions`, `ingestion_runs`
and `export_runs` came in FASE 4-5, `tracked_regions` with region selection,
and `ports` with congestion (FASE 10). No `port_activity` table exists on
purpose: congestion is derived on read (`app/ports.py`, `docs/architecture.md`
§15), so a precomputed activity snapshot would be a second source of truth
that ages exactly as it is being read.
"""

from __future__ import annotations

from app.models.export_run import ExportRun
from app.models.ingestion_run import IngestionRun
from app.models.port import Port
from app.models.region import TrackedRegion
from app.models.vessel import Vessel, VesselPosition

__all__ = ["ExportRun", "IngestionRun", "Port", "TrackedRegion", "Vessel", "VesselPosition"]