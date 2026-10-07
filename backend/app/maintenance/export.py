"""Archive a period of `vessel_positions`, then prune it — architecture §8.

The ordering is the whole design:

1. read every position past the retention window,
2. write the file and **read it back**,
3. delete exactly what the file holds and record the export — one transaction,
4. only then let the archive leave the machine.

A failure at any step leaves the rows in place with no `export_runs` row, so
the period is retried at the next run. Nothing is destroyed before it exists
somewhere else.

The delete is guarded by a rowcount rather than by trust: `DELETE … WHERE
timestamp < cutoff` must match the number of rows written, or the whole
transaction rolls back and the file is removed. That is what makes a
concurrent flush safe — a position committed between the read and the delete
appears as a mismatch, which aborts, instead of as a row nobody archived.

A hard kill between the file write and the commit leaves a file no `export_runs`
row mentions. That is the safe direction to fail: its rows are still in the
table and the next run archives them again, so the rule for anything reading
`EXPORT_DIR` is that the ledger — not the directory listing — enumerates the
archives. A graceful shutdown never reaches that window: the work runs in a
thread, and the threads are joined before the process exits.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import session_scope
from app.logging import get_logger
from app.models import ExportRun, VesselPosition

log = get_logger(__name__)

#: The export contract. The `SELECT`, the Arrow table and the CSV header all
#: derive from it, so a column cannot be added to one and forgotten in the
#: other two. `geom` is absent because the database derives it from
#: `latitude` and `longitude`, so it is reconstructed the moment anyone loads
#: the archive — see `docs/data-model.md` §2.
_SCHEMA = pa.schema(
    [
        pa.field("mmsi", pa.int32()),
        pa.field("timestamp", pa.timestamp("us", tz="UTC")),
        pa.field("latitude", pa.float64()),
        pa.field("longitude", pa.float64()),
        pa.field("sog", pa.float64()),
        pa.field("cog", pa.float64()),
        pa.field("heading", pa.int16()),
        pa.field("rot", pa.int16()),
        pa.field("nav_status", pa.int16()),
        pa.field("ship_name", pa.string()),
        pa.field("source", pa.string()),
        pa.field("received_at", pa.timestamp("us", tz="UTC")),
        pa.field("flags", pa.list_(pa.string())),
    ]
)


class ExportMismatch(RuntimeError):
    """The delete did not match the file. Nothing was committed."""


@dataclass(frozen=True, slots=True)
class ExportOutcome:
    """What one maintenance run moved out of the operational store."""

    period_start: datetime
    period_end: datetime
    row_count: int
    file: Path
    csv_file: Path | None
    byte_size: int


def export_and_prune(settings: Settings | None = None) -> ExportOutcome | None:
    """Archive every position older than the retention window, then delete it.

    Returns `None` when there is nothing to export: no file, no ledger row,
    and nothing touched.
    """
    settings = settings or get_settings()
    cutoff = datetime.now(UTC) - timedelta(days=settings.retention_days)
    started_at = datetime.now(UTC)
    written: list[Path] = []

    try:
        with session_scope() as session:
            records = _fetch(session, cutoff)
            if not records:
                return None

            period_start = min(record["timestamp"] for record in records)
            settings.export_dir.mkdir(parents=True, exist_ok=True)
            parquet = settings.export_dir / _file_name(period_start, cutoff)

            _write_parquet(parquet, records)
            written.append(parquet)
            _verify_parquet(parquet, len(records))

            csv_file: Path | None = None
            if settings.export_csv:
                csv_file = parquet.with_suffix(".csv")
                _write_csv(csv_file, records)
                written.append(csv_file)
                _verify_csv(csv_file, len(records))

            deleted = session.execute(
                delete(VesselPosition).where(VesselPosition.timestamp < cutoff)
            ).rowcount
            if deleted != len(records):
                raise ExportMismatch(
                    f"the archive holds {len(records)} rows but the delete matched "
                    f"{deleted}: the table moved while it was being written. "
                    f"Nothing was deleted."
                )

            session.add(
                ExportRun(
                    started_at=started_at,
                    finished_at=datetime.now(UTC),
                    period_start=period_start,
                    period_end=cutoff,
                    row_count=len(records),
                    file=parquet.name,
                    byte_size=parquet.stat().st_size,
                )
            )
    except BaseException:
        # Anything that stops this run before the commit must not leave a file
        # claiming rows the database still holds: the next run would archive
        # them again and the archive would carry them twice.
        for path in written:
            path.unlink(missing_ok=True)
        raise

    log.info(
        "retention: archived positions",
        extra={
            "rows": len(records),
            "period_start": period_start,
            "period_end": cutoff,
            "file": parquet.name,
            "csv": csv_file.name if csv_file is not None else None,
            "bytes": parquet.stat().st_size,
        },
    )
    return ExportOutcome(
        period_start=period_start,
        period_end=cutoff,
        row_count=len(records),
        file=parquet,
        csv_file=csv_file,
        byte_size=parquet.stat().st_size,
    )


def _file_name(period_start: datetime, period_end: datetime) -> str:
    """Name the archive after the period it covers, in a form every shell likes.

    Two runs can only collide by covering the same period, and the first run
    has already deleted it — so a collision means a retry of the same rows,
    which is precisely what overwriting is right for.
    """
    instant = "%Y-%m-%dT%H-%M-%SZ"
    return f"vessel_positions_{period_start:{instant}}_{period_end:{instant}}.parquet"


def _fetch(session: Session, cutoff: datetime) -> list[dict[str, Any]]:
    """Every position below `cutoff`, in track order.

    The list is the archive: the `SELECT` is written straight from the
    schema, and the `DELETE` later proves against this count that it removed
    the same rows.
    """
    statement = (
        select(*[getattr(VesselPosition, name) for name in _SCHEMA.names])
        .where(VesselPosition.timestamp < cutoff)
        .order_by(VesselPosition.timestamp, VesselPosition.mmsi)
    )
    records = [dict(row) for row in session.execute(statement).mappings()]
    for record in records:
        for field in _SCHEMA:
            if pa.types.is_timestamp(field.type):
                record[field.name] = record[field.name].astimezone(UTC)
    return records


def _write_parquet(path: Path, records: list[dict[str, Any]]) -> None:
    pq.write_table(pa.Table.from_pylist(records, schema=_SCHEMA), path)


def _verify_parquet(path: Path, expected: int) -> None:
    """Read the file back. A file that cannot be read is not an archive."""
    actual = pq.read_table(path).num_rows
    if actual != expected:
        raise ExportMismatch(
            f"{path.name} read back {actual} row(s), not the {expected} written"
        )


def _write_csv(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=_SCHEMA.names, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(_csv_row(record) for record in records)


def _verify_csv(path: Path, expected: int) -> None:
    with path.open(newline="", encoding="utf-8") as handle:
        actual = sum(1 for _ in csv.reader(handle)) - 1  # the header is not a row
    if actual != expected:
        raise ExportMismatch(
            f"{path.name} read back {actual} row(s), not the {expected} written"
        )


def _csv_row(record: dict[str, Any]) -> dict[str, str]:
    """Flatten what CSV has no type for: an instant, and a list of flags."""
    flat: dict[str, str] = {}
    for name, value in record.items():
        if value is None:
            flat[name] = ""
        elif isinstance(value, datetime):
            flat[name] = value.isoformat()
        elif isinstance(value, list):
            flat[name] = json.dumps(value, separators=(",", ":"))
        else:
            flat[name] = str(value)
    return flat
