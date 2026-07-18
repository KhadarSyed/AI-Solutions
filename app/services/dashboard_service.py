"""Stage 5 — dashboards: deterministic chart data first, LLM synthesis second
(grounded strictly in the computed data), cached as charts_data_file."""

import json
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import update

from app.analytics.chart_builders import impact, measurement, monitoring, narrative, reputation
from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.db.base import get_sessionmaker
from app.db.models import Session as SessionRow
from app.llm_gateway.guarded import GuardedAgent
from app.observability.logging import get_logger

log = get_logger(__name__)

DASHBOARDS = ("media_monitoring", "media_measurement", "pr_impact",
              "narrative_intelligence", "reputation_index")


class DashboardInsights(BaseModel):
    chart_insights: dict[str, str] = Field(
        description="chart-name → two-line, evidence-backed insight"
    )
    executive_summary: str = Field(description="3-5 sentence executive summary")
    storyboard: list[str] = Field(default_factory=list,
                                  description="3-6 bullet storyboard of the narrative")


async def _synthesize(kind: str, data: dict, brand: str) -> dict:
    agent = GuardedAgent(
        purpose=f"insights_{kind}", stage="dashboards",
        system_prompt=(
            "You write dashboard insights for PR analysts. Ground EVERY claim strictly in "
            "the chart data provided — numbers, names and dates must come from it verbatim. "
            "No invented facts. Two lines max per chart insight."
        ),
        output_type=DashboardInsights, temperature=0.0, cacheable=True,
    )
    compact = json.dumps(data, ensure_ascii=False, default=str)[:12000]
    try:
        result = await agent.run(f"Brand: {brand}\nDashboard: {kind}\nChart data:\n{compact}")
        return result.model_dump()
    except Exception as exc:
        log.warning("dashboards.synthesis_failed", kind=kind, error=str(exc)[:150])
        return {"chart_insights": {}, "executive_summary": "", "storyboard": []}


async def build_dashboards(*, session_id: str, force: bool = False) -> dict:
    store = get_artifact_store()
    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, uuid.UUID(session_id))
        if row is None:
            raise ValueError("session not found")
        if row.charts_data_file_key and not force:
            return await store.get_json(row.charts_data_file_key)   # cached
        config = row.config or {}
        project_id = str(row.project_id)

    tagged = await store.get_json(keys.tagged_file(session_id))
    approved = [a for a in tagged.get("articles", []) if a.get("is_approved")]
    monitoring_set = [a for a in tagged.get("articles", [])
                      if a.get("is_approved_for_monitoring")]

    brand = config.get("brand", "")
    competitors = config.get("competitors", [])
    sections = config.get("sections") or ["Brand News", "Competitors News", "Industry News"]

    dashboards = {
        "media_monitoring": monitoring.build(monitoring_set, sections),
        "media_measurement": measurement.build(approved),
        "pr_impact": impact.build(approved, brand, competitors),
        "narrative_intelligence": narrative.build(approved),
        "reputation_index": reputation.build(approved),
    }
    for kind, data in dashboards.items():
        data["insights"] = await _synthesize(kind, data, brand)

    payload = {
        "session_id": session_id, "project_id": project_id,
        "approved_count": len(approved), "monitoring_count": len(monitoring_set),
        "dashboards": dashboards,
    }
    key = keys.charts_data_file(session_id)
    await store.put_json(key, payload)
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(SessionRow).where(SessionRow.id == uuid.UUID(session_id))
            .values(charts_data_file_key=key, status="charts_ready")
        )
    log.info("dashboards.built", session=session_id,
             approved=len(approved), monitoring=len(monitoring_set))
    return payload
