"""Read-key enforcement for the data-serving routes.

The key is optional by design: with `API_READ_KEY` unset the API is open,
which is what development and the demo stack run. Setting it turns every route
that serves data into a keyed route.

`/health` is deliberately not one of them. A liveness probe that needs a secret
cannot be wired into a container healthcheck or a status page, and the payload
— version, bounding box, freshness — is operational metadata rather than data.
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
