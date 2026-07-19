"""API-key auth — Layer 9. The admin key is env-seeded; user keys land with Stage 1."""

import hashlib
import hmac
import re
from typing import Annotated

from fastapi import Cookie, Header, HTTPException, status

from app.config.settings import get_settings

SESSION_COOKIE = "prsol_session"

_TOKEN_RE = re.compile(r"\bRT-([0-9a-f]{16})\b")


def resume_token(run_id: str) -> str:
    """Per-run HMAC token embedded in gate notifications. A channel reply must
    carry it to resume the run — this survives sender spoofing because only a
    recipient of the original gate message has the token."""
    key = get_settings().admin_api_key.encode()
    digest = hmac.new(key, f"gate-resume:{run_id}".encode(), hashlib.sha256).hexdigest()[:16]
    return f"RT-{digest}"


def token_in_text(run_id: str, text: str) -> bool:
    expected = resume_token(run_id)
    for found in _TOKEN_RE.findall(text or ""):
        if hmac.compare_digest(f"RT-{found}", expected):
            return True
    return False


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
    prsol_session: Annotated[str | None, Cookie()] = None,
) -> None:
    # header (API clients) or httpOnly session cookie (the browser console)
    ok, reason = verify_api_key(x_api_key or prsol_session)
    if not ok:
        code = status.HTTP_401_UNAUTHORIZED if "required" in reason else status.HTTP_403_FORBIDDEN
        raise HTTPException(code, reason)
