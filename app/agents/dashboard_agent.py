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
    # Media Measurement sub-tabs (the agreed 5)
    "mm_overview": "Overview", "mm_sentiment": "Sentiment Analysis",
    "mm_themes": "Themes & Topics", "mm_coverage": "Media Coverage",
    "mm_stories": "Key Stories",
}

# every analytics chart regrouped under the 5 Media Measurement tabs
_MM_REMAP = {
    "coverage_trend": "mm_overview", "coverage_map": "mm_coverage",
    "competitive": "mm_overview", "competitive_matrix": "mm_overview",
    "impact_ranking": "mm_stories", "reputation_radar": "mm_overview",
    "section_heatmap": "mm_coverage", "top_themes": "mm_themes",
    "narrative_threads": "mm_themes", "top_publications": "mm_coverage",
}
_MM_TAB_ORDER = ("mm_overview", "mm_sentiment", "mm_themes", "mm_coverage", "mm_stories")


def _sentiment_donut(monitored: list[dict]) -> dict | None:
    from collections import Counter
    if not monitored:
        return None
    c = Counter(a.get("xai_sentiment", "NEU") for a in monitored)
    return {
        "id": "sentiment_donut", "engine": "echarts", "tab": "mm_sentiment",
        "title": "Sentiment Distribution",
        "option": {
            "tooltip": {"trigger": "item"}, "legend": {"bottom": 0},
            "series": [{"type": "pie", "radius": ["45%", "70%"], "data": [
                {"value": c.get("POS", 0), "name": "Positive",
                 "itemStyle": {"color": "#2f8f5b"}},
                {"value": c.get("NEU", 0), "name": "Neutral",
                 "itemStyle": {"color": "#9aa2ad"}},
                {"value": c.get("NEG", 0), "name": "Negative",
                 "itemStyle": {"color": "#dc2626"}}]}],
        },
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
    theme: str = "light"                                    # light | dark (light is default)
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
                if a.get("is_approved_for_monitoring") and a.get("country") not in ("", "all")
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


def _chat_config(session_id: str) -> dict:
    """Config the dashboard embeds so its in-report chat can call the API."""
    if not session_id:
        return {}
    from app.config.settings import get_settings
    from app.security.auth import chat_token

    s = get_settings()
    # Prefer the public base (set at deploy); empty → the widget uses window.location.origin
    # so a dashboard served from the API is same-origin and the chat just works.
    api_base = (s.public_api_base or s.chat_api_base).rstrip("/")
    return {"session_id": session_id, "token": chat_token(session_id),
            "api_base": api_base, "window_days": 3}


async def _daily_rows(session_id: str, limit: int = 400) -> list[dict]:
    """Approved articles as day-by-day rows for the Daily Monitoring view."""
    if not session_id:
        return []
    with contextlib.suppress(Exception):
        from app.artifacts import keys
        from app.artifacts.factory import get_artifact_store

        payload = await get_artifact_store().get_json(keys.tagged_file(session_id))
        rows = [
            {"id": a.get("id", ""),
             "date": str(a.get("published_date") or ""),
             "time": str(a.get("published_time") or ""),
             "title": a.get("title", ""),
             # the tagger's grounded summary; fall back to de-HTML'd content (never the raw
             # <a href> RSS redirect that used to show)
             "summary": (a.get("summary") or _plain(a.get("content", "")))[:280],
             "url": a.get("url", ""),
             "publisher": a.get("publisher_name") or a.get("publisher_domain", ""),
             "publisher_domain": a.get("publisher_domain", ""),
             "author": a.get("author", ""),
             "country": (a.get("country") or "").strip(),
             "reach": a.get("monthly_reach") or 0,
             "priority": bool(a.get("priority_watch")),
             "sentiment": a.get("xai_sentiment", "NEU"),
             "theme": a.get("theme_primary") or a.get("xai_theme", ""),
             "emotions": "; ".join(a.get("emotions", []) or []),
             "signals": "; ".join(a.get("signals", []) or []),
             "syndicated": [u for u in (a.get("syndicated_urls") or []) if u],
             "section": a.get("xai_section", "") or "Uncategorized"}
            for a in payload.get("articles", [])
            if a.get("is_approved_for_monitoring") and a.get("is_relevant", True) is not False
        ]
        _attach_similar(rows)
        return rows[:limit]
    return []


_CRISIS_RE = None


def _attach_similar(rows: list[dict]) -> None:
    """Cluster each row against the others and attach a `similar` list (why + link) so the
    Daily Monitoring card can show a 'similar articles' popup. Deterministic + explainable —
    reasons are Crisis / Same story / Same topic / Shared emotion, strongest first — so the
    read is grounded, not an opaque embedding score."""
    import re

    global _CRISIS_RE
    if _CRISIS_RE is None:
        _CRISIS_RE = re.compile(r"crisis|recall|lawsuit|litig|probe|fine|breach|scandal|"
                                r"layoff|fraud|outage|death|injur|contaminat", re.I)

    def _toks(s: str) -> set[str]:
        return set(re.findall(r"[a-z0-9]{4,}", (s or "").lower()))

    def _set(joined: str) -> set[str]:
        return {x.strip().lower() for x in (joined or "").split(";") if x.strip()}

    prep = []
    for r in rows:
        prep.append({"toks": _toks(r.get("title", "")),
                     "emo": _set(r.get("emotions", "")),
                     "sig": _set(r.get("signals", "")),
                     "crisis": bool(r.get("priority")) or bool(_CRISIS_RE.search(
                         f'{r.get("signals","")} {r.get("theme","")} {r.get("title","")}'))})

    for i, r in enumerate(rows):
        a = prep[i]
        sims: list[tuple[int, str, dict]] = []
        for j, o in enumerate(rows):
            if i == j or (r.get("url") and r["url"] == o.get("url")):
                continue
            b = prep[j]
            jac = (len(a["toks"] & b["toks"]) / len(a["toks"] | b["toks"])
                   if a["toks"] and b["toks"] else 0.0)
            same_topic = bool(r.get("theme")) and r.get("theme") == o.get("theme")
            shared_emo = bool(a["emo"] & b["emo"])
            crisis = a["crisis"] and b["crisis"] and (same_topic or a["sig"] & b["sig"])
            if crisis:
                score, why = 4, "Crisis"
            elif jac >= 0.55:
                score, why = 3, "Same story"
            elif same_topic:
                score, why = 2, "Same topic"
            elif shared_emo:
                score, why = 1, "Shared emotion"
            else:
                continue
            sims.append((score, why, {
                "title": o.get("title", ""), "publisher": o.get("publisher", ""),
                "url": o.get("url", ""), "date": o.get("date", ""),
                "sentiment": o.get("sentiment", "NEU"), "reason": why}))
        sims.sort(key=lambda t: t[0], reverse=True)
        r["similar"] = [s[2] for s in sims[:8]]


def _plain(html_or_text: str) -> str:
    """Strip HTML tags/entities so a card never shows a raw <a href> or markup."""
    import re
    t = re.sub(r"<[^>]+>", " ", html_or_text or "")
    t = re.sub(r"&[a-z#0-9]+;", " ", t)
    return re.sub(r"\s+", " ", t).strip()


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

    # Regroup analytics under the 5 Media Measurement tabs (the agreed design), and add a
    # sentiment donut so the Sentiment Analysis tab is populated.
    for c in charts:
        c["tab"] = _MM_REMAP.get(c["id"], "mm_overview")
    with contextlib.suppress(Exception):
        from app.artifacts import keys as _keys
        from app.artifacts.factory import get_artifact_store as _store

        if request.session_id:
            tagged = await _store().get_json(_keys.tagged_file(request.session_id))
            mon = [a for a in tagged.get("articles", []) if a.get("is_approved_for_monitoring")]
            donut = _sentiment_donut(mon)
            if donut:
                charts.insert(0, donut)
    tab_ids = [t for t in _MM_TAB_ORDER if any(c["tab"] == t for c in charts)]

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
        "daily": await _daily_rows(request.session_id),
        "chat": _chat_config(request.session_id),
        "requirements": request.requirements.model_dump(),
    }

    if request.session_id:
        await get_artifact_store().put_json(
            f"reports/{request.session_id}/dashboard_schema.json", schema
        )
    return schema
