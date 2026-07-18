"""API-key auth — Layer 9. The admin key is env-seeded; user keys land with Stage 1."""

import hmac
from typing import Annotated

from fastapi import Header, HTTPException, status

from app.config.settings import get_settings


def verify_api_key(candidate: str | None) -> tuple[bool, str]:
    """(ok, reason). Default/unset server credential is always refused."""
    expected = get_settings().admin_api_key
    if not candidate:
        return False, "API key required"
    if not expected or expected == "change-me-admin-key":
        return False, "ADMIN_API_KEY is unset/default — configure it in .env"
    if not hmac.compare_digest(candidate, expected):
        return False, "invalid API key"
    return True, ""


async def require_admin(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    ok, reason = verify_api_key(x_api_key)
    if not ok:
        code = status.HTTP_401_UNAUTHORIZED if "required" in reason else status.HTTP_403_FORBIDDEN
        raise HTTPException(code, reason)
