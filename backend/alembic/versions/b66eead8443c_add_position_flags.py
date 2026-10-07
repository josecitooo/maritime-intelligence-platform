"""add position flags

Revision ID: b66eead8443c
Revises: 58ed61b993fd
Create Date: 2026-10-06 22:03:23.704036
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'b66eead8443c'
down_revision: str | None = '58ed61b993fd'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A NOT NULL column cannot be added to a table that already holds rows
    # without a default to fill them with, so the default exists for exactly
    # as long as that statement needs it. Rows written before this rule have
    # no verdict, and `'{}'` says so honestly.
    #
    # It is then dropped: leaving `DEFAULT '{}'` would let an INSERT that
    # forgot `flags` succeed silently with an empty array, and this column is
    # only trustworthy if omitting it fails.
    op.add_column(
        'vessel_positions',
        sa.Column(
            'flags',
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default='{}',
        ),
    )
    op.alter_column('vessel_positions', 'flags', server_default=None)


def downgrade() -> None:
    op.drop_column('vessel_positions', 'flags')
