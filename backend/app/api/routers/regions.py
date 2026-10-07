"""`GET/PUT /regions` — the monitored-region catalog.

`GET` is open, like `/health`: the list of named regions is operational
metadata the browser needs before it has presented any credential, and it
says nothing about vessels. `PUT` deliberately requires a **separate** write
key: changing what the stream is asked about is not a data read, so
possession of the read key must not imply it (`app/api/auth.py`).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth import require_write_key
from app.db.session import session_scope
from app.regions import NoRegionEnabledError, UnknownRegionError, fetch_all, set_enabled
from app.schemas.regions import RegionResponse, RegionsUpdate

router = APIRouter(tags=["regions"])


def _to_response(region) -> RegionResponse:
    return RegionResponse(
        name=region.name,
        enabled=region.enabled,
        min_lat=region.min_lat,
        max_lat=region.max_lat,
        min_lon=region.min_lon,
        max_lon=region.max_lon,
    )


@router.get("/regions", response_model=list[RegionResponse])
def list_regions() -> list[RegionResponse]:
    """The whole catalog, enabled flags included, in alphabetical order.

    Intentionally not restricted to the enabled set: the selector needs to
    show every region the operator may turn on.
    """
    with session_scope() as session:
        return [_to_response(region) for region in fetch_all(session)]


@router.put(
    "/regions",
    response_model=list[RegionResponse],
    dependencies=[Depends(require_write_key)],
)
def update_regions(payload: RegionsUpdate) -> list[RegionResponse]:
    """Flip `enabled` for exactly the regions named; the rest keep their state.

    A disabled-selection is rejected with 422, because the stream asks about
    at least one box; an unknown name fails the whole request with 404 so a
    stale browser cannot silently stop a region it no longer knows about.
    """
    states = {update.name: update.enabled for update in payload.regions}
    with session_scope() as session:
        try:
            rows = set_enabled(session, states)
        except UnknownRegionError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Unknown region(s): {exc}",
            ) from exc
        except NoRegionEnabledError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return [_to_response(region) for region in rows]