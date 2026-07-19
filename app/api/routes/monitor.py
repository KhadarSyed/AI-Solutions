"""Serves the live-monitor UI shell and manages its session cookie.

The page is public; data it loads (/runs, /ws/runs) is authenticated. Entering
the key once POSTs it to /monitor/login, which validates it and sets an httpOnly
cookie — the browser then authenticates automatically (no re-entry, key never in
JS storage or the WS URL). /monitor/logout clears it."""

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Cookie, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from app.security.auth import (
    SESSION_COOKIE,
    create_session,
    revoke_session,
    valid_session,
    verify_api_key,
)

router = APIRouter(tags=["monitor"])

_HTML = (Path(__file__).resolve().parents[2] / "static" / "monitor.html").read_text(
    encoding="utf-8"
)

COOKIE_MAX_AGE = 30 * 24 * 3600  # 30 days


@router.get("/monitor", response_class=HTMLResponse)
async def monitor() -> HTMLResponse:
    return HTMLResponse(_HTML)


class LoginBody(BaseModel):
    key: str


def _is_https(request: Request) -> bool:
    return (request.url.scheme == "https"
            or request.headers.get("x-forwarded-proto", "").lower() == "https")


@router.post("/monitor/login")
async def login(body: LoginBody, request: Request, response: Response) -> dict:
    ok, reason = verify_api_key(body.key)
    if not ok:
        response.status_code = 401
        return {"ok": False, "reason": reason}
    token = await create_session()               # random id in Redis; raw key not stored
    response.set_cookie(
        SESSION_COOKIE, token,
        max_age=COOKIE_MAX_AGE, httponly=True, secure=_is_https(request),
        samesite="strict", path="/",
    )
    return {"ok": True}


@router.post("/monitor/logout")
async def logout(response: Response,
                 prsol_session: Annotated[str | None, Cookie()] = None) -> dict:
    await revoke_session(prsol_session)
    response.delete_cookie(SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/monitor/session")
async def session_status(
    prsol_session: Annotated[str | None, Cookie()] = None,
) -> dict:
    return {"authenticated": await valid_session(prsol_session)}
