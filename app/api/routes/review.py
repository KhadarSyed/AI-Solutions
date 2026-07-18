"""Stage 3 trigger + Stage 4 review endpoints (playbook: POST /tagging/{id},
PUT/POST/DELETE /tagging/...)."""

import uuid
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Session as SessionRow
from app.security.auth import require_admin
from app.services import review_service, tagging_service
from app.services.review_service import ReviewError

router = APIRouter(prefix="/tagging", tags=["tagging-review"],
                   dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


async def _ids(db: AsyncSession, session_id: uuid.UUID) -> tuple[str, str]:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return str(session_id), str(row.project_id)


@router.post("/{session_id}", status_code=202)
async def start_tagging(session_id: uuid.UUID, db: DB, background: BackgroundTasks) -> dict:
    sid, pid = await _ids(db, session_id)
    row = await db.get(SessionRow, session_id)
    if not row.source_file_key:
        raise HTTPException(409, "session has no collected articles yet")
    background.add_task(tagging_service.tag_session, session_id=sid, project_id=pid)
    return {"session_id": sid, "status": "tagging"}


@router.get("/{session_id}/articles")
async def list_tagged(session_id: uuid.UUID, db: DB,
                      offset: int = 0, limit: int = Query(50, le=200)) -> dict:
    sid, _ = await _ids(db, session_id)
    try:
        return await review_service.list_articles(sid, offset, limit)
    except Exception as exc:
        raise HTTPException(404, f"no tagged articles: {exc}") from exc


class EditBody(BaseModel):
    xai_sentiment: str | None = None
    xai_theme: str | None = None
    xai_section: str | None = None
    priority_watch: bool | None = None


@router.put("/{session_id}/articles/{article_id}")
async def edit_article(session_id: uuid.UUID, article_id: str, body: EditBody, db: DB) -> dict:
    sid, pid = await _ids(db, session_id)
    changes = {k: v for k, v in body.model_dump().items() if v is not None}
    if not changes:
        raise HTTPException(422, "no editable fields provided")
    try:
        return await review_service.edit_tags(
            project_id=pid, session_id=sid, article_id=article_id, changes=changes
        )
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc


class ApprovalBody(BaseModel):
    is_approved: bool | None = None
    is_approved_for_monitoring: bool | None = None


@router.post("/{session_id}/articles/{article_id}/approval")
async def approve_article(session_id: uuid.UUID, article_id: str,
                          body: ApprovalBody, db: DB) -> dict:
    sid, pid = await _ids(db, session_id)
    try:
        return await review_service.set_approval(
            project_id=pid, session_id=sid, article_id=article_id,
            is_approved=body.is_approved,
            is_approved_for_monitoring=body.is_approved_for_monitoring,
        )
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.delete("/{session_id}/articles/{article_id}")
async def delete_article(session_id: uuid.UUID, article_id: str, db: DB) -> dict:
    sid, pid = await _ids(db, session_id)
    try:
        remaining = await review_service.delete_article(
            project_id=pid, session_id=sid, article_id=article_id
        )
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"deleted": article_id, "remaining": remaining}


class AddBody(BaseModel):
    title: str
    url: str
    content: str = ""
    publisher_name: str = ""
    publisher_domain: str = ""
    xai_sentiment: str = "NEU"
    xai_theme: str = ""
    xai_section: str = "Brand News"


@router.post("/{session_id}/articles", status_code=201)
async def add_article(session_id: uuid.UUID, body: AddBody, db: DB) -> dict:
    sid, pid = await _ids(db, session_id)
    try:
        return await review_service.add_article(
            project_id=pid, session_id=sid, article=body.model_dump()
        )
    except ReviewError as exc:
        raise HTTPException(409, str(exc)) from exc
