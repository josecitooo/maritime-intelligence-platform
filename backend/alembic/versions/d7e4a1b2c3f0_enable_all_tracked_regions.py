"""enable every seeded region by default

Revision ID: d7e4a1b2c3f0
Revises: c1142f77a9e3
Create Date: 2026-10-08 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op

revision: str = "d7e4a1b2c3f0"
down_revision: str | None = "c1142f77a9e3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable the complete catalog for the default world view."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE tracked_regions "
            "SET enabled = TRUE, updated_at = :updated_at"
        ),
        {"updated_at": datetime.now(UTC)},
    )


def downgrade() -> None:
    """Restore the original legacy default region selection."""
    connection = op.get_bind()
    connection.execute(
        sa.text(
            "UPDATE tracked_regions "
            "SET enabled = (name = :legacy_name), updated_at = :updated_at"
        ),
        {
            "legacy_name": "Caribe y Golfo de México",
            "updated_at": datetime.now(UTC),
        },
    )
