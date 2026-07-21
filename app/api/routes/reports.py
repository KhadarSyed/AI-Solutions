"""Report archive — every completed run is a saved, retrievable report."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Run
from app.security.auth import require_admin

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


@router.get("")
async def list_reports(
    db: DB, brand: str | None = Query(None), limit: int = Query(50, le=200)
) -> dict:
    """Every completed pipeline run, newest first — Task ID, brand, date, live link.
    Each report is also openable at /dashboards/{session_id} and /report/{session_id}."""
    rows = (
        await db.execute(
            select(Run)
            .where(Run.graph_name == "pipeline", Run.status == "completed")
            .order_by(desc(Run.created_at))
            .limit(limit * 3)  # over-fetch, brand-filter in Python (brand lives in JSONB)
        )
    ).scalars().all()

    out = []
    for r in rows:
        addr = r.origin_address or {}
        rbrand = addr.get("brand", "")
        if brand and brand.lower() not in (rbrand or "").lower():
            continue
        out.append({
            "task_id": addr.get("task_id", ""),
            "brand": rbrand,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "session_id": str(r.session_id) if r.session_id else None,
            "dashboard_url": addr.get("dashboard_url"),
            "dashboard_path": f"/dashboards/{r.session_id}" if r.session_id else None,
            "report_path": f"/report/{r.session_id}" if r.session_id else None,
            "monitoring_count": addr.get("monitoring_count"),
        })
        if len(out) >= limit:
            break
    return {"reports": out, "count": len(out)}
