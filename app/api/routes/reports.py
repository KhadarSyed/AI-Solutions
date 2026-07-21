"""Report archive — every completed run is a saved, retrievable report."""
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.base import get_db
from app.db.models import Run
from app.db.models import Session as SessionRow
from app.security.auth import require_admin
from app.services.vercel_publisher import project_name

router = APIRouter(prefix="/reports", tags=["reports"], dependencies=[Depends(require_admin)])

DB = Annotated[AsyncSession, Depends(get_db)]


async def report_rows(db: AsyncSession, brand: str | None, limit: int) -> list[dict]:
    """Every completed pipeline run, newest first — Task ID, brand (from the session),
    date, and the live link (persisted, or reconstructed from the canonical Vercel name)."""
    rows = (
        await db.execute(
            select(Run, SessionRow)
            .join(SessionRow, Run.session_id == SessionRow.id, isouter=True)
            .where(Run.graph_name == "pipeline", Run.status == "completed")
            .order_by(desc(Run.created_at))
            .limit(limit * 3)
        )
    ).all()

    out: list[dict] = []
    for run, sess in rows:
        addr = run.origin_address or {}
        rbrand = addr.get("brand") or (sess.config or {}).get("brand", "") if sess else ""
        task_id = addr.get("task_id", "")
        if brand and brand.lower() not in (rbrand or "").lower():
            continue
        url = addr.get("dashboard_url")
        if not url and rbrand and task_id:   # reconstruct the canonical public URL
            url = f"https://{project_name(rbrand, task_id)}.vercel.app"
        out.append({
            "task_id": task_id, "brand": rbrand,
            "created_at": run.created_at.isoformat() if run.created_at else None,
            "session_id": str(run.session_id) if run.session_id else None,
            "dashboard_url": url,
            "dashboard_path": f"/dashboards/{run.session_id}" if run.session_id else None,
            "report_path": f"/report/{run.session_id}" if run.session_id else None,
            "monitoring_count": addr.get("monitoring_count"),
        })
        if len(out) >= limit:
            break
    return out


@router.get("")
async def list_reports(
    db: DB, brand: str | None = Query(None), limit: int = Query(50, le=200)
) -> dict:
    reports = await report_rows(db, brand, limit)
    return {"reports": reports, "count": len(reports)}
