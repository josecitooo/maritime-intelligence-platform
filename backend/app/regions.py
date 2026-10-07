"""Shared repository for the monitored-region catalog.

Two consumers need the same rows: the API serves the catalog over HTTP and
`PUT /regions` flips the `enabled` flags; the worker reads the enabled set
and turns it into the aisstream bounding boxes it subscribes with. Keeping
the queries here means the two can never disagree about what "the current
selection" means.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from sqlalchemy import select

from app.models import TrackedRegion

if TYPE_CHECKING:
    from sqlalchemy.orm import Session


class UnknownRegionError(ValueError):
    """A write named a region that is not in the catalogue."""


class NoRegionEnabledError(ValueError):
    """A write would leave the stream monitoring nothing."""


def fetch_all(session: Session) -> list[TrackedRegion]:
    """Every catalogue row, alphabetically."""
    return list(session.scalars(select(TrackedRegion).order_by(TrackedRegion.name)))


def fetch_enabled(session: Session) -> list[TrackedRegion]:
    """The rows currently subscribed, alphabetically.

    An empty list is meaningful: it means the stream is asked about nothing.
    The API refuses to *create* that state, but an operator editing the table
    directly is outside the API's rules, so consumers still handle it.
    """
    return list(
        session.scalars(
            select(TrackedRegion).where(TrackedRegion.enabled).order_by(TrackedRegion.name)
        )
    )


def set_enabled(session: Session, states: dict[str, bool]) -> list[TrackedRegion]:
    """Flip `enabled` for exactly the named regions and return the catalog.

    * regions not named keep their state;
    * an unknown name raises `UnknownRegionError`;
    * a write that would leave every region disabled raises
      `NoRegionEnabledError`, because aisstream needs at least one box.
    """
    known = {row.name: row for row in session.scalars(select(TrackedRegion))}
    unknown = sorted(set(states) - set(known))
    if unknown:
        raise UnknownRegionError(",".join(unknown))

    for name, enabled in states.items():
        known[name].enabled = enabled
        known[name].updated_at = datetime.now(UTC)

    if not any(row.enabled for row in known.values()):
        raise NoRegionEnabledError("the stream needs at least one enabled region")

    return fetch_all(session)


def bounding_boxes(regions: list[TrackedRegion]) -> list[list[list[float]]]:
    """The aisstream wire shape: ``[[[max_lat, min_lon], [min_lat, max_lon]], ...]``.

    This mirrors `Settings.aisstream_bounding_boxes` but for the selected set:
    a mismatch here would subscribe to boxes nobody asked for, and the two
    keepers of the shape would silently disagree.
    """
    return [[[row.max_lat, row.min_lon], [row.min_lat, row.max_lon]] for row in regions]