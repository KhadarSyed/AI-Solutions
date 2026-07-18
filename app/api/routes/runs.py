import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Run
from app.orchestration.run_manager import get_run_manager
from app.security.auth import require_admin

router = APIRouter(prefix="/runs", tags=["runs"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


class StartRunBody(BaseModel):
    graph: str = "pipeline"
    session_id: str | None = None
    origin_channel: str = "web"
    origin_address: dict = Field(default_factory=dict)
    input: dict = Field(default_factory=dict)


class ResumeBody(BaseModel):
    decision: str | None = None
    feedback: str = ""
    payload: dict | None = None


def _serialize(r: Run) -> dict:
    return {
        "id": str(r.id),
        "session_id": str(r.session_id) if r.session_id else None,
        "graph": r.graph_name,
        "thread_id": r.thread_id,
        "status": r.status,
        "origin_channel": r.origin_channel,
        "awaiting_input": r.awaiting_input,
        "heartbeat_at": r.heartbeat_at.isoformat() if r.heartbeat_at else None,
        "error": r.error,
        "created_at": r.created_at.isoformat(),
    }


@router.post("", status_code=201)
async def start_run(body: StartRunBody) -> dict:
    try:
        run_id = await get_run_manager().start(
            graph_name=body.graph,
            input_state=body.input,  # type: ignore[arg-type]
            session_id=body.session_id,
            origin_channel=body.origin_channel,
            origin_address=body.origin_address,
        )
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": run_id}


@router.get("")
async def list_runs(db: DB, status: str | None = None, limit: int = Query(50, le=200)) -> dict:
    q = select(Run).order_by(Run.created_at.desc()).limit(limit)
    if status:
        q = q.where(Run.status == status)
    rows = (await db.execute(q)).scalars().all()
    return {"runs": [_serialize(r) for r in rows]}


@router.get("/{run_id}")
async def get_run(run_id: uuid.UUID, db: DB) -> dict:
    run = (await db.execute(select(Run).where(Run.id == run_id))).scalar_one_or_none()
    if run is None:
        raise HTTPException(404, "run not found")
    return _serialize(run)


@router.post("/{run_id}/pause")
async def pause_run(run_id: uuid.UUID) -> dict:
    try:
        await get_run_manager().pause(str(run_id))
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "paused"}


@router.post("/{run_id}/resume")
async def resume_run(run_id: uuid.UUID, body: ResumeBody) -> dict:
    resume_payload = body.payload
    if resume_payload is None and body.decision is not None:
        resume_payload = {"decision": body.decision, "feedback": body.feedback}
    try:
        await get_run_manager().resume(str(run_id), resume_payload)
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"status": "running"}


@router.post("/{run_id}/restart", status_code=201)
async def restart_run(run_id: uuid.UUID, body: StartRunBody) -> dict:
    try:
        new_id = await get_run_manager().restart(str(run_id), body.input)  # type: ignore[arg-type]
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"run_id": new_id}


@router.post("/{run_id}/cancel")
async def cancel_run(run_id: uuid.UUID) -> dict:
    await get_run_manager().cancel(str(run_id))
    return {"status": "cancelled"}