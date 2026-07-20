"""Dashboard Agent — reusable schema producer any business agent can call.

Flow: data fetch → calculations → AI summaries → chart selection (memory/graph
biased) → tabs/layout (liked-template aware) → DashboardSchema artifact.
The HtmlRenderer turns the schema into one self-contained dashboard.html."""

import contextlib
from collections import Counter
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.analytics.chart_selector import SelectionPrefs, select_charts
from app.artifacts.factory import get_artifact_store
from app.llm_gateway.guarded import GuardedAgent
from app.memory.mem0_service import MemoryType, recall
from app.observability.logging import get_logger
from app.services import template_store

log = get_logger(__name__)

TAB_LABELS = {
    "overview": "Overview", "coverage": "Coverage", "competitive": "Competitive",
    "reputation": "Reputation", "narratives": "Narratives", "insights": "AI Insights",
}


class DashboardRequirements(BaseModel):
    include_charts: bool = True
    include_tabs: bool = True
    include_ai_summary: bool = True
    include_calculations: bool = True
    include_geo: bool = True
    include_video_banner: bool = True
    include_logos: bool = True


class DashboardRequest(BaseModel):
    title: str
    project_id: str
    session_id: str | None = None
    user_id: str | None = None
    data_sources: list[str] = Field(default_factory=list)   # artifact://, service://, inline handled by caller
    requirements: DashboardRequirements = Field(default_factory=DashboardRequirements)
    theme: str = "dark"                                     # dark | light
    reuse_liked_template: bool = True
    feedback_context: str = ""                              # latest user feedback to honor


class ExecSummary(BaseModel):
    executive: list[str] = Field(description="3-6 bullet executive summary, data-grounded")
    recommendations: list[str] = Field(description="2-4 actionable recommendations")


async def _fetch_boards(request: DashboardRequest) -> dict:
    """Resolve data sources. Session dashboards use the Stage-5 service; explicit
    artifact:// keys are loaded verbatim."""
    store = get_artifact_store()
    boards: dict = {}
    for src in request.data_sources:
        if src.startswith("artifact://"):
            with contextlib.suppress(Exception):
                boards.update(await store.get_json(src.removeprefix("artifact://")))
    if not boards and request.session_id:
        from app.services.dashboard_service import build_dashboards

        payload = await build_dashboards(session_id=request.session_id)
        boards = payload["dashboards"]
        boards["_approved_count"] = payload["approved_count"]
        # geo counts from the tagged corpus (country field from the enricher)
        with contextlib.suppress(Exception):
            from app.artifacts import keys as akeys

            tagged = await store.get_json(akeys.tagged_file(request.session_id))
            counts = Counter(
                a.get("country", "all") for a in tagged.get("articles", [])
                if a.get("is_approved") and a.get("country") not in ("", "all")
            )
            if counts:
                boards["_geo_counts"] = dict(counts)
    return boards


def _kpis(boards: dict) -> list[dict]:
    m = boards.get("media_measurement", {}).get("kpis", {})
    p = boards.get("pr_impact", {}).get("gauge", {})
    r = boards.get("reputation_index", {})
    kpis = []
    if m:
        kpis += [
            {"label": "Articles", "value": m.get("total", 0)},
            {"label": "Positive", "value": f"{m.get('positive_pct', 0)}%",
             "tone": "good"},
            {"label": "Negative", "value": f"{m.get('negative_pct', 0)}%",
             "tone": "bad" if m.get("negative_pct", 0) > 25 else "neutral"},
            {"label": "Total Reach", "value": f"{m.get('total_reach', 0):,}"},
        ]
    if p:
        kpis.append({"label": "PR Gauge", "value": p.get("rating", "—"),
                     "sub": f"avg {p.get('daily_average', 0)}/day"})
    if r:
        kpis.append({"label": "Reputation", "value": r.get("composite", 0.0),
                     "sub": "of 100"})
    return kpis


async def _selection_prefs(request: DashboardRequest) -> SelectionPrefs:
    texts: list[str] = []
    hot: list[str] = []
    with contextlib.suppress(Exception):
        for mtype in (MemoryType.PROFILE, MemoryType.FEEDBACK):
            hits = await recall(agent="dashboard", project_id=request.project_id,
                                user_id=request.user_id,
                                query="dashboard chart layout preferences",
                                memory_type=mtype, limit=5)
            texts += [str(h.get("memory", "")) for h in hits]
    with contextlib.suppress(Exception):
        # graph-backed semantic recall: hot competitor relations promote competitive charts
        hits = await recall(agent="dashboard", project_id=request.project_id,
                            query="strongest competitor rivalry relations",
                            memory_type=MemoryType.SEMANTIC, limit=4)
        hot = [str(h.get("memory", "")) for h in hits if "competitor" in str(h).lower()]
    if request.feedback_context:
        texts.append(request.feedback_context)
    return SelectionPrefs.from_memories(texts, hot)


async def _summaries(request: DashboardRequest, boards: dict, kpis: list[dict]) -> dict:
    if not request.requirements.include_ai_summary:
        return {"executive": [], "recommendations": []}
    agent = GuardedAgent(
        purpose="dashboard_summary", stage="dashboards",
        system_prompt=(
            "You write executive summaries for PR dashboards. Ground every bullet in the "
            "provided KPI and chart data — verbatim numbers only, no invention. "
            "If user feedback is provided, address it explicitly."
        ),
        output_type=ExecSummary, temperature=0.0, cacheable=True,
    )
    import json

    compact = json.dumps({"kpis": kpis, "boards": boards}, default=str)[:11000]
    try:
        result = await agent.run(
            f"Title: {request.title}\nUser feedback to honor: {request.feedback_context or 'none'}\n"
            f"Data:\n{compact}"
        )
        return result.model_dump()
    except Exception as exc:
        log.warning("dashboard_agent.summary_failed", error=str(exc)[:150])
        return {"executive": [], "recommendations": []}


class _ChartInsight(BaseModel):
    chart_id: str = Field(description="echo the chart id exactly")
    insight: str = Field(description="a crisp two-line insight grounded in this chart's numbers")


class _ChartInsights(BaseModel):
    items: list[_ChartInsight]


async def _chart_insights(charts: list[dict]) -> dict[str, str]:
    """One batched call: a grounded 2-line insight per chart, keyed by chart id."""
    if not charts:
        return {}
    import json

    compact = [{"id": c["id"], "title": c.get("title", ""),
                "data": json.dumps(c.get("option") or {"geo": c.get("geo")}, default=str)[:700]}
               for c in charts]
    agent = GuardedAgent(
        purpose="chart_insights", stage="dashboards",
        system_prompt=(
            "For each chart, write a crisp TWO-LINE insight grounded ONLY in that "
            "chart's numbers — verbatim values, no invention, no preamble. Echo the "
            "chart_id exactly. Return one entry per input chart."),
        output_type=_ChartInsights, temperature=0.0, cacheable=True,
    )
    try:
        res = await agent.run(json.dumps(compact, default=str)[:11000])
        return {i.chart_id: i.insight.strip() for i in res.items if i.insight}
    except Exception as exc:
        log.warning("dashboard_agent.chart_insights_failed", error=str(exc)[:150])
        return {}


async def build_schema(request: DashboardRequest) -> dict:
    boards = await _fetch_boards(request)
    if not request.requirements.include_geo:
        boards.pop("_geo_counts", None)

    kpis = _kpis(boards) if request.requirements.include_calculations else []
    prefs = await _selection_prefs(request)

    template = None
    if request.reuse_liked_template:
        template = await template_store.load_liked(request.project_id, request.user_id)
        if template:
            request.theme = template.get("theme", request.theme)
            for cid, ctype in (template.get("chart_types") or {}).items():
                prefs.preferred_types[cid] = ctype

    charts = select_charts(boards, prefs) if request.requirements.include_charts else []
    if charts and request.requirements.include_ai_summary:
        insights = await _chart_insights(charts)
        for c in charts:
            c["insight"] = insights.get(c["id"], "")

    tab_ids = [t for t in ("overview", "coverage", "competitive", "reputation", "narratives")
               if any(c["tab"] == t for c in charts)]
    if request.requirements.include_ai_summary:
        tab_ids.append("insights")
    if template and template.get("tab_order"):
        order = {tid: i for i, tid in enumerate(template["tab_order"])}
        tab_ids.sort(key=lambda t: order.get(t, 99))

    summaries = await _summaries(request, {k: v for k, v in boards.items()
                                           if not k.startswith("_")}, kpis)

    logos: dict = {}
    if request.requirements.include_logos:
        with contextlib.suppress(Exception):
            from app.tools.enrichment.logos import resolve_logos

            sov = boards.get("pr_impact", {}).get("share_of_voice", {})
            names = [sov.get("brand")] if sov.get("brand") else []
            names += [c["name"] for c in sov.get("competitors", [])][:4]
            if names:
                resolved = await resolve_logos(request.project_id, names)
                logos = {"brand": resolved[0], "competitors": resolved[1:]}

    banner = {}
    if request.requirements.include_video_banner:
        banner = dict((template or {}).get("banner") or {})
        if not banner.get("video_url"):
            with contextlib.suppress(Exception):
                import uuid

                from app.db.base import get_sessionmaker
                from app.db.models import Project
                from app.tools.enrichment.pexels import brand_video

                sov = boards.get("pr_impact", {}).get("share_of_voice", {}) or {}
                brand = sov.get("brand") or request.title
                async with get_sessionmaker()() as db:
                    proj = await db.get(Project, uuid.UUID(request.project_id))
                    industry = proj.industry if proj else None
                vid = await brand_video(brand, industry)
                if vid:
                    banner = {"video_url": vid["video_url"], "poster": vid.get("poster", ""),
                              "credit": "Pexels"}
        if not banner.get("video_url"):
            banner = {
                "video_url": "https://videos.pexels.com/video-files/3129957/3129957-hd_1920_1080_25fps.mp4",
                "credit": "Pexels",
            }

    schema = {
        "title": request.title,
        "theme": request.theme,
        "generated_at": datetime.now(UTC).isoformat(),
        "template_reused": bool(template),
        "tabs": [{"id": t, "label": TAB_LABELS.get(t, t.title())} for t in tab_ids],
        "kpis": kpis,
        "charts": charts,
        "summaries": summaries,
        "logos": logos,
        "banner": banner,
        "requirements": request.requirements.model_dump(),
    }

    if request.session_id:
        await get_artifact_store().put_json(
            f"reports/{request.session_id}/dashboard_schema.json", schema
        )
    return schema
