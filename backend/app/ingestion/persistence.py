"""Persist one validated window — §4's `insert ► ingestion_runs`.

Two rules shape this module.

**One transaction.** The run row and the rows it counts commit together, so
`ingestion_runs` can never advertise data that is not there, nor omit data
that is. It also means a failed write leaves the window untouched: the caller
has lost nothing by retrying, and there is no `status` column to disagree with
reality.

**Nothing is written that validation did not vouch for.** `process_window` has
already rejected what must not be stored, and this module writes only from the
`WindowResult` it is handed. The one thing it reads —
`load_previous_positions` — fetches the *past*, which is never a candidate for
writing, and it happens before the window is judged so `position_jump` has an
anchor to measure against.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.session import session_scope
from app.ingestion.pipeline import PreviousPosition, WindowResult, dimensions
from app.models import IngestionRun, Vessel, VesselPosition
from app.providers.base import PositionSample, StaticSample

#: The descriptive columns of `vessels` that a later observation may refresh.
#: Identity (`mmsi`) and the stamp are handled separately.
_MERGEABLE = (
    "name",
    "callsign",
    "imo",
    "ship_type",
    "length_m",
    "width_m",
    "draught_m",
    "destination",
    "eta",
    "source",
)


def load_previous_positions(mmsis: Collection[int]) -> dict[int, PreviousPosition]:
    """The last *stored* position of each vessel, as of before this window.

    A window cannot supply this itself: it holds what arrived since the last
    flush, and `position_jump` is precisely the claim that the two disagree.
    The worker fetches these anchors before calling `process_window`, so the
    rule stays pure and the read stays out of the write path.

    `DISTINCT ON (mmsi)` rides the primary key `(mmsi, timestamp)`, which is
    already in track order, so "latest row per vessel" is a property of the
    index rather than a sort.
    """
    if not mmsis:
        return {}
    with session_scope() as session:
        rows = session.execute(
            select(
                VesselPosition.mmsi,
                VesselPosition.timestamp,
                VesselPosition.latitude,
                VesselPosition.longitude,
            )
            .where(VesselPosition.mmsi.in_(mmsis))
            .order_by(VesselPosition.mmsi, VesselPosition.timestamp.desc())
            .ext(distinct_on(VesselPosition.mmsi))
        )
        return {
            mmsi: PreviousPosition(timestamp=timestamp, latitude=latitude, longitude=longitude)
            for mmsi, timestamp, latitude, longitude in rows
        }


def persist_window(
    result: WindowResult,
    *,
    window_start: datetime | None,
    window_end: datetime,
    reason: str,
) -> None:
    """Write a window and its run record atomically.

    Runs in one transaction by construction: the caller must not be able to
    commit one without the other.
    """
    with session_scope() as session:
        _insert_positions(session, result.positions, result.row_flags)
        _upsert_vessels(session, result.statics)
        session.add(
            IngestionRun(
                window_start=window_start,
                window_end=window_end,
                reason=reason,
                positions=len(result.positions),
                statics=len(result.statics),
                vessels=result.vessels,
                throttled=result.throttled,
                evicted=result.evicted,
                rejected=result.rejected,
                flagged=result.flagged,
            )
        )


# ── positions ───────────────────────────────────────────────────────────


def _insert_positions(
    session: Session,
    positions: Sequence[PositionSample],
    row_flags: Mapping[tuple[int, datetime], frozenset[str]],
) -> None:
    """Add the window's positions, ignoring any already stored.

    `ON CONFLICT DO NOTHING` over `(mmsi, timestamp)` is what makes a
    replayed window or a crash mid-flush harmless (`docs/ingestion.md` §6):
    the batch is either written or written again, never doubled. Flags ride
    the row for the same reason — a replay rewrites the identical verdict.
    """
    if not positions:
        return
    session.execute(
        pg_insert(VesselPosition)
        .values(
            [
                _position_row(
                    sample,
                    row_flags.get((sample.mmsi, sample.timestamp), frozenset()),
                )
                for sample in positions
            ]
        )
        .on_conflict_do_nothing(index_elements=["mmsi", "timestamp"])
    )


def _position_row(sample: PositionSample, flags: frozenset[str]) -> dict[str, object]:
    """Map a decoded position onto its columns.

    `geom` is absent because the database derives it from `latitude` and
    `longitude` — passing it here would be rejected by Postgres as a write to
    a generated column. `flags` is sorted so a replayed window writes the
    same bytes twice.
    """
    return {
        "mmsi": sample.mmsi,
        "timestamp": sample.timestamp,
        "latitude": sample.latitude,
        "longitude": sample.longitude,
        "cog": sample.cog,
        "sog": sample.sog,
        "heading": sample.heading,
        "rot": sample.rot,
        "nav_status": sample.nav_status,
        "ship_name": sample.ship_name,
        "source": sample.source,
        "received_at": sample.received_at,
        "flags": sorted(flags),
    }


# ── identity ────────────────────────────────────────────────────────────


def _upsert_vessels(session: Session, statics: Sequence[StaticSample]) -> None:
    """Merge each vessel's identity, never erasing a value it already has.

    The buffer's `merge_static` only fills blanks, because within one window a
    type 24 part A (name only) must not wipe a ship type learned from a type 5.
    Across windows the rule has to admit change: `destination` and `draught`
    belong to the current voyage, and freezing them at first sighting would
    turn `vessels` into a museum. So a field the incoming message *did not
    carry* keeps the stored value, and one it did carry replaces it.
    """
    if not statics:
        return

    stmt = pg_insert(Vessel).values([_vessel_row(sample) for sample in statics])
    updates = {
        name: func.coalesce(stmt.excluded[name], getattr(Vessel, name))
        for name in _MERGEABLE
    }
    # An out-of-order observation must not age the row backwards.
    updates["updated_at"] = func.greatest(stmt.excluded.updated_at, Vessel.updated_at)

    session.execute(stmt.on_conflict_do_update(index_elements=["mmsi"], set_=updates))


def _vessel_row(sample: StaticSample) -> dict[str, object]:
    """The wire's four hull offsets already turned into a size.

    `dim_a..dim_d` and `draught` are the sample's fields; the table wants
    `length_m`/`width_m` (§9) and `draught_m`. Both halves of a dimension are
    required, so a partially known size stores `NULL` rather than half a
    vessel.
    """
    length, width = dimensions(sample)
    return {
        "mmsi": sample.mmsi,
        "name": sample.name,
        "callsign": sample.callsign,
        "imo": sample.imo,
        "ship_type": sample.ship_type,
        "length_m": length,
        "width_m": width,
        "draught_m": sample.draught,
        "destination": sample.destination,
        "eta": sample.eta,
        "source": sample.source,
        "updated_at": sample.received_at,
    }
