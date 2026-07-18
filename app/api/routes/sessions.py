import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.db.base import get_db
from app.db.models import Session as SessionRow
from app.security.auth import require_admin
from app.services import ingestion_service
from app.tools.connectors.file_upload import parse_upload

router = APIRouter(prefix="/sessions", tags=["sessions"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


async def _session(db: AsyncSession, session_id: uuid.UUID) -> SessionRow:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    return row


@router.get("/{session_id}")
async def get_session(session_id: uuid.UUID, db: DB) -> dict:
    s = await _session(db, session_id)
    return {
        "id": str(s.id), "project_id": str(s.project_id), "status": s.status,
        "config": s.config, "articles": s.articles_count, "error": s.error,
        "source_file_key": s.source_file_key, "tagged_file_key": s.tagged_file_key,
        "charts_data_file_key": s.charts_data_file_key,
    }


@router.post("/{session_id}/upload")
async def upload_articles(session_id: uuid.UUID, file: UploadFile, db: DB) -> dict:
    s = await _session(db, session_id)
    data = await file.read()
    try:
        articles = parse_upload(file.filename or "upload.csv", data)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    await get_artifact_store().put_bytes(
        keys.upload(session_id, file.filename or "upload.bin"), data,
        file.content_type or "application/octet-stream",
    )
    stats = await ingestion_service.collect(
        session_id=str(session_id),
        brand=s.config.get("brand", ""),
        query_groups=[],                      # upload-only path: no fleet fan-out
        extra_articles=articles,
    )
    return {"session_id": str(session_id), **stats}


@router.post("/{session_id}/collect", status_code=202)
async def collect_now(session_id: uuid.UUID, db: DB) -> dict:
    """Direct (non-graph) collection trigger — runs the fleet with the session config."""
    s = await _session(db, session_id)
    config = s.config or {}
    if not config.get("query_groups"):
        raise HTTPException(422, "session config has no query_groups (run the query builder)")
    stats = await ingestion_service.collect(
        session_id=str(session_id),
        brand=config.get("brand", ""),
        query_groups=config["query_groups"],
    )
    return {"session_id": str(session_id), **stats}
