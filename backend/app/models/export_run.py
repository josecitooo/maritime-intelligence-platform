"""`export_runs` — one row per archive that was written, verified and pruned.

This table exists because a `DELETE` cannot explain itself. Architecture §8
makes deletion conditional on a successful export for the period, and the only
durable way to say "period *X* was archived" is a row. Without it, retention
would be a claim in a log file that nobody can query.

Like `ingestion_runs`, it has **no `status` column**: the row and the `DELETE`
it authorises commit together, so an export that fails leaves neither — no row
claiming success, no rows deleted. The failure exists only as an error in the
log, and the period is retried at the next maintenance run.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ExportRun(Base):
    """One period of `vessel_positions` moved out of the operational store."""

    __tablename__ = "export_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    #: `[period_start, period_end)` in **message time**, not wall clock. The
    #: first export starts wherever the archive begins; every later one starts
    #: where the previous one finished, because the rows before it are gone.
    period_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    #: The retention cutoff — `now - RETENTION_DAYS`. Everything below it was
    #: exported and then deleted in this same transaction.
    period_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    row_count: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Path relative to `EXPORT_DIR`; the Parquet file is the archive of
    #: record, whether or not a CSV copy was written beside it.
    file: Mapped[str] = mapped_column(Text, nullable=False)
    byte_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
