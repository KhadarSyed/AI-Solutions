"""API-key auth — Layer 9. The admin key is env-seeded; user keys land with Stage 1."""

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config.settings import get_settings


async def require_admin(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    expected = get_settings().admin_api_key
    if not x_api_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "X-API-Key header required")
    if not expected or expected == "change-me-admin-key":
        # refuse to run privileged endpoints on a default credential
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "ADMIN_API_KEY is unset/default — configure it in .env"
        )
    if not hmac.compare_digest(x_api_key, expected):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "invalid API key")
