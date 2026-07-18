"""Dashboard Agent endpoints — HTML delivery, like (template save), feedback
(immediate regeneration)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.dashboard_agent import DashboardRequest, build_schema
from app.artifacts.base import ArtifactNotFound
from app.artifacts.factory import get_artifact_store
from app.db.base import get_db
from app.db.models import Session as SessionRow
from app.memory.mem0_service import MemoryType, remember
from app.security.auth import require_admin, verify_api_key
from app.services import template_store
from app.services.html_renderer import render

router = APIRouter(prefix="/dashboards", tags=["dashboards"])

DB = Annotated[AsyncSession, Depends(get_db)]


async def _generate(session_id: str, db: AsyncSession, *, theme: str | None = None,
                    feedback: str = "") -> str:
    row = await db.get(SessionRow, uuid.UUID(session_id))
    if row is None:
        raise HTTPException(404, "session not found")
    request = DashboardRequest(
        title=f"{row.config.get('brand', 'Brand')} — Media Intelligence",
        project_id=str(row.project_id),
        session_id=session_id,
        feedback_context=feedback,
    )
    if theme:
        request.theme = theme
        request.reuse_liked_template = False if theme else request.reuse_liked_template
    schema = await build_schema(request)
    html = render(schema)
    await get_artifact_store().put_bytes(
        f"reports/{session_id}/dashboard.html", html.encode(), "text/html"
    )
    return html


@router.get("/{session_id}", response_class=HTMLResponse)
async def get_dashboard(
    session_id: uuid.UUID, db: DB,
    api_key: str | None = Query(None),
    theme: str | None = Query(None, pattern="^(dark|light)$"),
    regenerate: bool = False,
) -> HTMLResponse:
    ok, reason = verify_api_key(api_key)
    if not ok:
        raise HTTPException(401, reason)
    store = get_artifact_store()
    if not regenerate and theme is None:
        try:
            cached = await store.get_bytes(f"reports/{session_id}/dashboard.html")
            return HTMLResponse(cached.decode())
        except ArtifactNotFound:
            pass
    html = await _generate(str(session_id), db, theme=theme)
    return HTMLResponse(html)


class FeedbackBody(BaseModel):
    message: str
    user_id: str | None = None


@router.post("/{session_id}/feedback", dependencies=[Depends(require_admin)])
async def dashboard_feedback(session_id: uuid.UUID, body: FeedbackBody, db: DB) -> dict:
    """Store feedback (graph learns via Mem0 extraction) and regenerate NOW."""
    row = await db.get(SessionRow, uuid.UUID(str(session_id)))
    if row is None:
        raise HTTPException(404, "session not found")
    import contextlib

    with contextlib.suppress(Exception):
        await remember(
            agent="dashboard", memory_type=MemoryType.FEEDBACK,
            project_id=str(row.project_id), user_id=body.user_id,
            content=f"Dashboard feedback: {body.message}",
        )
    await _generate(str(session_id), db, feedback=body.message)
    return {"status": "regenerated",
            "dashboard_url": f"/dashboards/{session_id}?api_key=<key>"}


@router.post("/{session_id}/like", dependencies=[Depends(require_admin)])
async def like_dashboard(session_id: uuid.UUID, db: DB,
                         user_id: str | None = None) -> dict:
    """Persist the current layout as the standing template for this project/user."""
    row = await db.get(SessionRow, uuid.UUID(str(session_id)))
    if row is None:
        raise HTTPException(404, "session not found")
    try:
        schema = await get_artifact_store().get_json(
            f"reports/{session_id}/dashboard_schema.json"
        )
    except ArtifactNotFound as exc:
        raise HTTPException(409, "no dashboard generated yet for this session") from exc
    template = await template_store.save_liked(str(row.project_id), schema, user_id)
    return {"status": "template_saved", "template": template}
