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


# ── browser session tokens (random id in Redis; raw key never in the cookie) ──

SESSION_TTL = 30 * 24 * 3600
_SESSION_PREFIX = "monitor:session:"


def _redis():
    import redis.asyncio as aioredis

    return aioredis.from_url(get_settings().redis_url, decode_responses=True)


async def create_session() -> str:
    import secrets

    token = secrets.token_urlsafe(32)
    r = _redis()
    try:
        await r.set(_SESSION_PREFIX + token, "1", ex=SESSION_TTL)
    finally:
        await r.aclose()
    return token


async def valid_session(token: str | None) -> bool:
    if not token:
        return False
    r = _redis()
    try:
        return bool(await r.get(_SESSION_PREFIX + token))
    except Exception:
        return False
    finally:
        await r.aclose()


async def revoke_session(token: str | None) -> None:
    if not token:
        return
    r = _redis()
    try:
        await r.delete(_SESSION_PREFIX + token)
    finally:
        await r.aclose()


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
    # API clients present the key header; the browser console presents its
    # httpOnly session cookie (a random Redis-backed token, not the raw key)
    if x_api_key:
        ok, reason = verify_api_key(x_api_key)
        if not ok:
            code = (status.HTTP_401_UNAUTHORIZED if "required" in reason
                    else status.HTTP_403_FORBIDDEN)
            raise HTTPException(code, reason)
        return
    if await valid_session(prsol_session):
        return
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "authentication required")
