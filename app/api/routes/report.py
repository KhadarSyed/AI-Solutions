import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.report_builder import build_report
from app.db.base import get_db
from app.db.models import Session as SessionRow
from app.security.auth import require_admin

router = APIRouter(prefix="/report", tags=["report"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/{session_id}")
async def get_report(
    session_id: uuid.UUID, db: DB,
    template: str | None = Query(None, pattern="^(beone|trane|otsuka)$"),
    date_from: date | None = None,
    date_to: date | None = None,
) -> Response:
    row = await db.get(SessionRow, session_id)
    if row is None:
        raise HTTPException(404, "session not found")
    if not row.charts_data_file_key:
        raise HTTPException(409, "build dashboards before generating the report")
    brand = (row.config or {}).get("brand", "")
    try:
        data = await build_report(
            session_id=str(session_id), brand=brand, template=template,
            date_from=date_from, date_to=date_to,
        )
    except Exception as exc:
        raise HTTPException(500, f"report build failed: {exc}") from exc
    filename = f"{(brand or 'brand').lower().replace(' ', '_')}_report.docx"
    return Response(content=data, media_type=DOCX_MIME,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})
