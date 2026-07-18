import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Session as SessionRow
from app.security.auth import require_admin
from app.services.dashboard_service import DASHBOARDS, build_dashboards

router = APIRouter(prefix="/charts", tags=["charts"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("/{session_id}")
async def get_charts(
    session_id: uuid.UUID, db: DB,
    dashboard: str | None = Query(None, description=f"one of {DASHBOARDS}"),
    force: bool = False,
) -> dict:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    if not row.tagged_file_key:
        raise HTTPException(409, "session has no tagged articles yet")
    try:
        payload = await build_dashboards(session_id=str(session_id), force=force)
    except Exception as exc:
        raise HTTPException(500, f"dashboard build failed: {exc}") from exc

    if dashboard:
        if dashboard not in payload["dashboards"]:
            raise HTTPException(422, f"unknown dashboard {dashboard}")
        return {"session_id": str(session_id), dashboard: payload["dashboards"][dashboard]}
    return payload
