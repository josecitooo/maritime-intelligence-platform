"""ORM models.

Alembic imports this package so every model registers its metadata before
autogenerate runs. Four tables are introduced by FASE 4 and 5 — `vessels`,
`vessel_positions`, `ingestion_runs` and `export_runs`. The rest of the design
arrives with the phase that consumes it rather than as empty scaffolding:
`ports` and `port_activity` with congestion (FASE 10).
"""

from __future__ import annotations

from app.models.export_run import ExportRun
from app.models.ingestion_run import IngestionRun
from app.models.vessel import Vessel, VesselPosition

__all__ = ["ExportRun", "IngestionRun", "Vessel", "VesselPosition"]
