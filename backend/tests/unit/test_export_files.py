"""The archive file itself: written, read back, and named the same every time.

None of this needs the database. Whether a Parquet file round-trips is a
property of the writer, and finding out here is far cheaper than finding out
in the middle of a retention window — by which point the rows it failed to
preserve are already gone.
"""

from __future__ import annotations

import csv
from datetime import UTC, datetime
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from app.maintenance.export import (
    ExportMismatch,
    _file_name,
    _verify_csv,
    _verify_parquet,
    _write_csv,
    _write_parquet,
)

T0 = datetime(2026, 9, 1, 0, 0, 0, tzinfo=UTC)
T1 = datetime(2026, 9, 29, 12, 0, 0, tzinfo=UTC)

#: What the archive is supposed to carry. Asserted literally, not against the
#: schema, so that changing `_SCHEMA` is a decision someone has to make here
#: too rather than a change that quietly redefines the test.
COLUMNS = (
    "mmsi",
    "timestamp",
    "latitude",
    "longitude",
    "sog",
    "cog",
    "heading",
    "rot",
    "nav_status",
    "ship_name",
    "source",
    "received_at",
    "flags",
)


def record(**overrides: object) -> dict[str, object]:
    fields: dict[str, object] = dict(
        mmsi=355693000,
        timestamp=T0,
        latitude=10.5,
        longitude=-61.5,
        sog=9.0,
        cog=180.0,
        heading=181,
        rot=-1,
        nav_status=0,
        ship_name="EXAMPLE",
        source="aisstream.io",
        received_at=T0,
        flags=[],
    )
    fields.update(overrides)
    return fields


def test_every_declared_column_survives_the_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "archive.parquet"
    _write_parquet(path, [record(flags=["position_jump"], sog=None, ship_name=None)])

    table = pq.read_table(path)

    assert tuple(table.schema.names) == COLUMNS
    assert table.num_rows == 1
    stored = table.to_pylist()[0]
    assert stored["flags"] == ["position_jump"]
    assert stored["sog"] is None
    assert stored["ship_name"] is None
    assert stored["timestamp"] == T0


def test_a_file_whose_rows_do_not_add_up_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "archive.parquet"
    _write_parquet(path, [record()])

    with pytest.raises(ExportMismatch, match="not the 2 written"):
        _verify_parquet(path, 2)


def test_a_file_that_cannot_be_read_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "archive.parquet"
    path.write_bytes(b"certainly not a parquet file")

    with pytest.raises(pa.ArrowInvalid):
        _verify_parquet(path, 1)


def test_the_csv_copy_states_what_a_spreadsheet_has_no_type_for(tmp_path: Path) -> None:
    path = tmp_path / "archive.csv"
    _write_csv(path, [record(flags=["position_jump"], heading=None)])

    with path.open(newline="", encoding="utf-8") as handle:
        header, *rows = list(csv.reader(handle))

    assert tuple(header) == COLUMNS
    assert rows[0][COLUMNS.index("timestamp")] == "2026-09-01T00:00:00+00:00"
    assert rows[0][COLUMNS.index("flags")] == '["position_jump"]'
    assert rows[0][COLUMNS.index("heading")] == ""


def test_the_csv_row_count_excludes_the_header(tmp_path: Path) -> None:
    path = tmp_path / "archive.csv"
    _write_csv(path, [record(), record(mmsi=355693001, timestamp=T1)])

    _verify_csv(path, 2)  # two data rows, one header

    with pytest.raises(ExportMismatch, match="not the 1 written"):
        _verify_csv(path, 1)


def test_the_file_name_describes_the_period_and_survives_a_shell() -> None:
    name = _file_name(T0, T1)

    assert name == (
        "vessel_positions_2026-09-01T00-00-00Z_2026-09-29T12-00-00Z.parquet"
    )
    # `:` is what makes a filename unusable on Windows and in a cron mail.
    assert ":" not in name
    assert Path(name).suffix == ".parquet"
