"""Read- and write-key enforcement for the API routes.

The read key is optional by design: with `API_READ_KEY` unset the data routes
are open, which is what development and the demo stack run. Setting it turns
every route that serves data into a keyed route.

The write key works the other way around: with `API_WRITE_KEY` unset, writes
are *disabled*, not open. Changing what the stream is asked about deserves a
key just like the data reads demand, and "no key configured" must not mean
"anyone can repoint the stream".

`/health` and `GET /regions` are deliberately not keyed. A liveness probe
that needs a secret cannot be wired into a container healthcheck, and the
region catalog is the same kind of operational metadata: the browser needs
it before it has presented any credential, and it says nothing about vessels.
"""

from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, status

from app.config import get_settings


async def require_read_key(
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
) -> None:
    """Reject the request unless `X-API-Key` matches the configured read key.

    Compared in constant time: the key is a shared secret, and a comparison
    that short-circuits on the first differing byte is a clock to measure.
    """
    settings = get_settings()
    if not settings.auth_required:
        return
    supplied = x_api_key or ""
    if not hmac.compare_digest(supplied, settings.api_read_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-API-Key header is required",
            headers={"WWW-Authenticate": "ApiKey"},
        )


async def require_write_key(
    x_write_key: str | None = Header(default=None, alias="X-Write-Key"),
) -> None:
    """Authorise a mutation: 403 when writes are not configured, 401 on mismatch.

    Both answers are deliberate. A deployment that never set `API_WRITE_KEY`
    offers no write surface at all — not an open one. One that did gets the
    same constant-time comparison the read key uses, because a wrong key must
    not be measurable by timing either.
    """
    settings = get_settings()
    if not settings.write_auth_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Region changes are disabled: API_WRITE_KEY is not configured",
        )
    supplied = x_write_key or ""
    if not hmac.compare_digest(supplied, settings.api_write_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="A valid X-Write-Key header is required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
