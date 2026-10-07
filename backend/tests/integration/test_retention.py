"""Retention against a real database: archive first, prune second, never the reverse.

`docs/architecture.md` §8 makes deletion conditional on a successful export for
the period. A unit test can show the file is written and read back; only a
database can show that the ordering holds in fact — that a failed archive
leaves every row where it was, that a successful one leaves a ledger row
describing exactly what it took, and that what it took still reads.

Run them with the test database up (README §4). They do not skip.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pyarrow.parquet as pq
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db.session import session_scope
from app.ingestion.buffer import WindowBatch
from app.ingestion.persistence import persist_window
from app.ingestion.pipeline import process_window
from app.maintenance.export import export_and_prune
from app.models import ExportRun, Vessel, VesselPosition
from app.providers.base import PositionSample, StaticSample

pytestmark = pytest.mark.integration

#: A point inside the default bounding box (Santo Domingo).
LATITUDE = 18.4717
LONGITUDE = -69.9300

NOW = datetime.now(UTC)
#: Past the 7-day cutoff, and comfortably past it whatever hour the suite runs.
OLD = NOW - timedelta(days=9)
#: Inside the window, so it must survive untouched.
FRESH = NOW - timedelta(hours=6)


def position(mmsi: int, timestamp: datetime) -> PositionSample:
    return PositionSample(
        mmsi=mmsi,
        timestamp=timestamp,
        latitude=LATITUDE,
        longitude=LONGITUDE,
        source="aisstream.io",
        received_at=timestamp + timedelta(seconds=4),
    )


def static(mmsi: int) -> StaticSample:
    """Only a static message creates a `vessels` row — a position never does."""
    return StaticSample(mmsi=mmsi, received_at=NOW, source="aisstream.io", name="ARCHIVE TEST")


def write(
    positions: list[PositionSample], statics: list[StaticSample] | None = None
) -> None:
    """Persist through the real write path, not through raw SQL."""
    statics = statics or []
    result = process_window(
        WindowBatch(
            positions=positions,
            statics=statics,
            vessels=len({sample.mmsi for sample in positions + statics}),
            throttled=0,
            evicted=0,
        )
    )
    persist_window(result, window_start=None, window_end=datetime.now(UTC), reason="test")


def settings_for(export_dir: Path, *, export_csv: bool = False) -> Settings:
    """Settings that read neither `.env` nor whatever the shell exported."""
    return Settings(
        _env_file=None,
        aisstream_api_key="integration",
        retention_days=7,
        export_dir=export_dir,
        export_csv=export_csv,
    )


def count_of(model) -> int:
    with session_scope() as session:
        return session.scalar(select(func.count()).select_from(model))


def test_old_positions_are_archived_then_pruned(tmp_path: Path) -> None:
    write(
        [
            position(373_123_456, OLD),
            position(373_123_457, OLD + timedelta(minutes=10)),
            position(373_123_456, FRESH),
        ],
        statics=[static(373_123_456)],
    )

    outcome = export_and_prune(settings_for(tmp_path))

    assert outcome is not None
    assert outcome.row_count == 2
    assert OLD < outcome.period_end < FRESH
    assert outcome.period_start == OLD

    with session_scope() as session:
        remaining = session.scalars(select(VesselPosition.timestamp)).all()
    assert remaining == [FRESH]
    # Scope is `vessel_positions` alone: the ship itself is not history.
    assert count_of(Vessel) == 1

    with session_scope() as session:
        run = session.scalar(select(ExportRun))
    assert run is not None
    assert run.row_count == outcome.row_count
    assert run.period_start == outcome.period_start
    assert run.period_end == outcome.period_end
    assert run.file == outcome.file.name
    assert run.byte_size == outcome.byte_size == outcome.file.stat().st_size
    assert run.started_at <= run.finished_at

    archived = pq.read_table(outcome.file)
    assert archived.num_rows == 2
    assert set(archived.column("mmsi").to_pylist()) == {373_123_456, 373_123_457}
    assert min(archived.column("timestamp").to_pylist()) == OLD


def test_a_failed_archive_leaves_every_row_and_no_ledger_entry(tmp_path: Path) -> None:
    """The whole guarantee, in one case: nothing goes before something arrives."""
    write([position(373_123_456, OLD), position(373_123_457, OLD)])
    blocked = tmp_path / "not-a-directory"
    blocked.write_text("", encoding="utf-8")

    with pytest.raises(FileExistsError):
        export_and_prune(settings_for(blocked))

    assert count_of(VesselPosition) == 2
    assert count_of(ExportRun) == 0
    assert [entry.name for entry in tmp_path.iterdir()] == ["not-a-directory"]


def test_a_period_with_nothing_to_archive_touches_nothing(tmp_path: Path) -> None:
    write([position(373_123_456, FRESH)])

    assert export_and_prune(settings_for(tmp_path)) is None

    assert count_of(VesselPosition) == 1
    assert count_of(ExportRun) == 0
    assert list(tmp_path.iterdir()) == []


def test_an_optional_csv_copy_reads_back_with_the_same_rows(tmp_path: Path) -> None:
    write([position(373_123_456, OLD), position(373_123_457, OLD)])

    outcome = export_and_prune(settings_for(tmp_path, export_csv=True))

    assert outcome is not None and outcome.csv_file is not None
    with outcome.csv_file.open(newline="", encoding="utf-8") as handle:
        rows = list(csv.reader(handle))
    assert len(rows) - 1 == outcome.row_count  # the header is not a row
