"""`GET/PUT /regions` — the monitored-region catalog contract.

Reads return the whole catalog (name, box, `enabled`). Writes carry only the
change: `PUT /regions` flips `enabled` for exactly the regions named and
leaves every other row untouched. Boxes are defined by the seed migration
and are not writable.
"""

from __future__ import annotations

from pydantic import BaseModel


class RegionResponse(BaseModel):
    """One catalogue row as served to browsers."""

    name: str
    enabled: bool
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float


class RegionUpdate(BaseModel):
    """One flag flip in a write."""

    name: str
    enabled: bool


class RegionsUpdate(BaseModel):
    """The set of flag flips a browser wants applied.

    Names not present keep their current state; an unknown name fails the
    whole request rather than being ignored, so a stale UI cannot silently
    stop monitoring a region it no longer knows about.
    """

    regions: list[RegionUpdate]