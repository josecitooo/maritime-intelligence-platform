"""ORM models.

Alembic imports this package so every model registers its metadata before
autogenerate runs. Three tables are introduced in FASE 4 — `vessels`,
`vessel_positions` and `ingestion_runs`. The rest of the design arrives with
the phase that consumes it rather than as empty scaffolding: `export_runs`
with retention (FASE 5), `ports` and `port_activity` with congestion
(FASE 10).
"""

from __future__ import annotations

from app.models.ingestion_run import IngestionRun
from app.models.vessel import Vessel, VesselPosition

__all__ = ["IngestionRun", "Vessel", "VesselPosition"]
