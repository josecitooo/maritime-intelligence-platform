"""`ingestion_runs` — one row per closed window, empty ones included.

The table is both an operational record and the thing that keeps an idle
Supabase free-tier project from being paused: a flush that found nothing still
writes this row, so the database hears from us every
`INGESTION_INTERVAL_MINUTES` whether or not any vessel moved.

Counters are the window's **validation verdict**, not a rowcount. They answer
"what did the flush decide", alongside the rejected and flagged breakdowns; a
duplicate suppressed by `ON CONFLICT DO NOTHING` was still an accepted row.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class IngestionRun(Base):
    """How one flush went, written in the same transaction as its data.

    There is no `status` column because there is no failed run to record: the
    run row and the positions it counts share a transaction, so a write that
    fails leaves no row claiming success, and a write that succeeds leaves the
    window drained. A failed flush is visible as a gap in `window_end` and as
    an error in the log — see `docs/data-model.md`.

    The transport counters (`frames`, `decode_errors`, `unusable`,
    `reconnects`) are deliberately absent. They are cumulative for the
    process lifetime, so they would be the one column here that resets to
    zero on every restart, and `WindowResult` — the shape this table exists
    to persist — does not carry them. They go to the structured log at every
    window close; stream health is exposed by `/health`.
    """

    __tablename__ = "ingestion_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    window_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: `scheduled` | `shutdown` | whatever the caller named the flush.
    reason: Mapped[str] = mapped_column(Text, nullable=False)

    positions: Mapped[int] = mapped_column(Integer, nullable=False)
    statics: Mapped[int] = mapped_column(Integer, nullable=False)
    vessels: Mapped[int] = mapped_column(Integer, nullable=False)
    throttled: Mapped[int] = mapped_column(Integer, nullable=False)
    evicted: Mapped[int] = mapped_column(Integer, nullable=False)

    #: reason -> count, aggregated at flush (`docs/ingestion.md` §5).
    rejected: Mapped[dict] = mapped_column(JSONB, nullable=False)
    flagged: Mapped[dict] = mapped_column(JSONB, nullable=False)
