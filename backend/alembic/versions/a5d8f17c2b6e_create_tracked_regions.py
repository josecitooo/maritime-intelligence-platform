"""create tracked_regions and seed the region catalog

Revision ID: a5d8f17c2b6e
Revises: 351c64ece721
Create Date: 2026-10-07 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "a5d8f17c2b6e"
down_revision: str | None = "351c64ece721"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: One catalogued region per row: the box is fixed by this migration, and the
#: browser only ever flips `enabled`. The default selection keeps exactly the
#: legacy `MIN_LAT/...` bounding box — Caribbean and Gulf of Mexico — so a
#: fresh deployment behaves exactly as it did before regions existed.
#: Names are Spanish because that is what the UI shows; identifiers stay English.
_SEEDED_AT = datetime(2026, 10, 7, 0, 30, tzinfo=UTC)
_CATALOG = [
    # (name, min_lat, max_lat, min_lon, max_lon, enabled)
    ("Caribe y Golfo de México", 8.0, 31.0, -98.0, -59.0, True),
    ("Costa este de Estados Unidos", 31.0, 45.0, -81.0, -65.0, False),
    ("Pacífico oriental y Panamá", -10.0, 15.0, -95.0, -70.0, False),
    ("Costa atlántica de Sudamérica", -45.0, -5.0, -62.0, -30.0, False),
    ("Canal de la Mancha y Mar del Norte", 48.0, 61.0, -10.0, 9.0, False),
    ("Mediterráneo occidental", 30.0, 47.0, -10.0, 15.0, False),
    ("Mediterráneo oriental y Suez", 28.0, 42.0, 15.0, 44.0, False),
    ("Golfo Pérsico", 22.0, 30.0, 47.0, 58.0, False),
    ("África occidental y Golfo de Guinea", -5.0, 10.0, -20.0, 10.0, False),
    ("Océano Índico occidental", -30.0, 5.0, 30.0, 75.0, False),
    ("Estrecho de Malaca y Sudeste Asiático", -14.0, 14.0, 88.0, 122.0, False),
    ("Japón y Asia oriental", 20.0, 45.0, 118.0, 150.0, False),
]


def upgrade() -> None:
    op.create_table(
        "tracked_regions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False, unique=True),
        sa.Column("min_lat", sa.Float(), nullable=False),
        sa.Column("max_lat", sa.Float(), nullable=False),
        sa.Column("min_lon", sa.Float(), nullable=False),
        sa.Column("max_lon", sa.Float(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    regions = sa.table(
        "tracked_regions",
        sa.column("name", sa.Text),
        sa.column("min_lat", sa.Float),
        sa.column("max_lat", sa.Float),
        sa.column("min_lon", sa.Float),
        sa.column("max_lon", sa.Float),
        sa.column("enabled", sa.Boolean),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        regions,
        [
            {
                "name": name,
                "min_lat": min_lat,
                "max_lat": max_lat,
                "min_lon": min_lon,
                "max_lon": max_lon,
                "enabled": enabled,
                "updated_at": _SEEDED_AT,
            }
            for name, min_lat, max_lat, min_lon, max_lon, enabled in _CATALOG
        ],
    )


def downgrade() -> None:
    op.drop_table("tracked_regions")