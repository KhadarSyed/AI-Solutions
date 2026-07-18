import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Project
from app.db.models import Session as SessionRow
from app.security.auth import require_admin

router = APIRouter(prefix="/projects", tags=["projects"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


class ProjectBody(BaseModel):
    name: str
    brand_name: str
    industry: str | None = None
    section_taxonomy: list[str] = Field(
        default=["Brand News", "Competitors News", "Industry News"]
    )
    stakeholder_emails: list[str] = Field(default_factory=list)


@router.post("", status_code=201)
async def create_project(body: ProjectBody, db: DB) -> dict:
    project = Project(
        name=body.name, brand_name=body.brand_name, industry=body.industry,
        section_taxonomy=body.section_taxonomy, stakeholder_emails=body.stakeholder_emails,
    )
    db.add(project)
    await db.commit()
    return {"project_id": str(project.id)}


@router.get("")
async def list_projects(db: DB) -> dict:
    rows = (await db.execute(select(Project).order_by(Project.created_at.desc()))).scalars()
    return {"projects": [
        {"id": str(p.id), "name": p.name, "brand_name": p.brand_name,
         "industry": p.industry, "sections": p.section_taxonomy}
        for p in rows
    ]}


@router.post("/{project_id}/sessions", status_code=201)
async def create_session(project_id: uuid.UUID, db: DB, config: dict | None = None) -> dict:
    project = await db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "project not found")
    row = SessionRow(project_id=project_id, config=config or {})
    db.add(row)
    await db.commit()
    return {"session_id": str(row.id)}


@router.get("/{project_id}/sessions")
async def list_sessions(project_id: uuid.UUID, db: DB) -> dict:
    rows = (
        await db.execute(
            select(SessionRow).where(SessionRow.project_id == project_id)
            .order_by(SessionRow.created_at.desc())
        )
    ).scalars()
    return {"sessions": [
        {"id": str(s.id), "status": s.status, "articles": s.articles_count,
         "source_file_key": s.source_file_key, "tagged_file_key": s.tagged_file_key,
         "charts_data_file_key": s.charts_data_file_key, "created_at": s.created_at.isoformat()}
        for s in rows
    ]}
