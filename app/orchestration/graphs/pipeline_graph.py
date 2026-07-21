"""The pipeline graph with THREE channel-delivered human gates:

  plan_gate  (Stage 1) — approve the plan (brands, window, goal, boolean queries) before
                         any searching happens; changes loop back here.
  collect_gate (Stage 2) — approve the collected coverage KPIs + the tagging plan; the
                         user can add data points (persisted to the Tagging agent).
  tagged_gate (Stage 3) — sign off the tagged set (themes/signals/SOV/sentiment); an
                         edited CSV drops rows from monitoring.

Every gate replies IN-THREAD on the origin channel with a styled, interactive email.
Studio loads the module-level `graph`; RunManager compiles with the Postgres checkpointer.
"""

import re
import uuid
from datetime import UTC, datetime, timedelta

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.channels.csv_export import COLLECTED_COLUMNS, TAGGED_COLUMNS, articles_to_csv
from app.observability.logging import get_logger
from app.orchestration.state import PipelineState

log = get_logger(__name__)

_PALETTE = ["#b5462f", "#2563eb", "#16a34a", "#a855f7", "#d97706", "#0891b2", "#db2777"]


def _brand_color(name: str) -> str:
    return _PALETTE[sum(map(ord, name)) % len(_PALETTE)] if name else _PALETTE[0]


def _tid(state: PipelineState) -> str:
    return state.get("task_id") or (state.get("origin_address") or {}).get("task_id") or ""


async def _session_config(session_id: str) -> dict:
    from app.db.base import get_sessionmaker
    from app.db.models import Session as SessionRow

    async with get_sessionmaker()() as db:
        row = await db.get(SessionRow, uuid.UUID(session_id))
        return (row.config or {}) if row else {}


async def _progress(state: PipelineState, message: str) -> None:
    import contextlib

    with contextlib.suppress(Exception):
        from app.channels.notifier import notify_progress

        await notify_progress(state, message)


async def _agent(state: PipelineState, agent: str, phase: str, message: str) -> None:
    import contextlib

    from app.channels.notifier import notify_agent
    with contextlib.suppress(Exception):
        await notify_agent(state, agent, phase, message)


def _gate(payload: dict) -> dict:
    """Human approval point. Normally interrupt() until a channel reply resumes the run;
    when AUTO_APPROVE_GATES is set the stage notification has already gone out, so we
    self-approve and let the pipeline run end-to-end over the real channel."""
    from app.config.settings import get_settings

    if get_settings().auto_approve_gates:
        log.info("gate.auto_approved", gate=payload.get("gate"))
        return {"decision": "approved", "feedback": ""}
    return interrupt(payload)


def _duration(days_back: int) -> tuple[str, str]:
    """(human label, days) for the plan window."""
    days = max(1, round(days_back))
    end = datetime.now(UTC).date()
    start = end - timedelta(days=days)
    return (f"{start:%d %b} – {end:%d %b %Y} (last {days} day{'s' if days != 1 else ''})",
            str(days))


# ------------------------------------------------------------------ Stage 1: plan gate
async def _apply_plan_changes(session_id: str, feedback: str) -> None:
    """Best-effort natural-language plan edits: add/remove competitors, change the window."""
    from app.db.base import get_sessionmaker
    from app.db.models import Project
    from app.db.models import Session as SessionRow
    from app.services.query_plan import build_query_plan

    fb = (feedback or "").lower()
    async with get_sessionmaker()() as db, db.begin():
        row = await db.get(SessionRow, uuid.UUID(session_id))
        if row is None:
            return
        config = dict(row.config or {})
        competitors = list(config.get("competitors", []))
        before = [c.lower() for c in competitors]
        for m in re.finditer(r"add (?:competitor|brand|rival)s?\s+([a-z0-9 ,&.\-]+)", fb):
            for name in re.split(r",| and ", m.group(1)):
                nm = name.strip().title()
                if nm and nm.lower() not in [c.lower() for c in competitors]:
                    competitors.append(nm)
        for m in re.finditer(r"(?:remove|drop|exclude) (?:competitor|brand)s?\s+([a-z0-9 ,&.\-]+)", fb):
            for name in re.split(r",| and ", m.group(1)):
                nm = name.strip().lower()
                competitors = [c for c in competitors if c.lower() != nm]
        dm = re.search(r"(?:last|past)\s+(\d+)\s+day", fb)
        if dm:
            config["days_back"] = int(dm.group(1))
        if [c.lower() for c in competitors] != before:
            proj = await db.get(Project, row.project_id)
            config["competitors"] = competitors
            config["query_groups"] = build_query_plan(
                config.get("brand", ""), competitors, proj.industry if proj else None)
        row.config = config


async def plan_gate(state: PipelineState) -> dict:
    from app.channels import stage_report
    from app.channels.notifier import notify_stage
    from app.security.auth import resume_token

    session_id = state["session_id"]
    config = await _session_config(session_id)
    brand = config.get("brand", state.get("brand", "Brand"))
    competitors = config.get("competitors", state.get("competitors", []))
    query_groups = config.get("query_groups", state.get("query_groups", []))
    import contextlib

    from app.config.settings import get_settings
    days_back = config.get("days_back", get_settings().collection_days_back)
    label, _ = _duration(days_back)

    logos: dict = {}
    with contextlib.suppress(Exception):
        from app.tools.enrichment.logos import logo_urls

        logos = await logo_urls(state["project_id"], [brand, *competitors])
    brands_logos = [(b, logos.get(b), _brand_color(b)) for b in [brand, *competitors]]
    intent = (f"Monitor {brand} and {len(competitors)} competitor(s) across news and social "
              f"for {label}. Classify each article for sentiment (with confidence and a "
              "reason), theme tiers, emotions, signals and entities, then deliver an "
              "approved, board-ready dashboard.")
    goal = (f"Give {brand}'s PR team a daily, decision-ready view of coverage, share of "
            "voice, sentiment and emerging signals.")
    queries = [q for g in query_groups for q in g.get("queries", [])]
    token = resume_token(state.get("run_id", ""))

    with contextlib.suppress(Exception):
        html = stage_report.plan_html(
            _tid(state), brand, {}, brands_logos=brands_logos, duration=label,
            intent=intent, goal=goal, boolean_queries=queries)
        html += _ref_footer(token)
        await notify_stage(state, stage_key="plan", html=html, message=(
            f"Monitoring begins for {brand}. Review the plan and reply APPROVE to start "
            f"collecting, or 'change: …' to adjust. Reference: {token}"))

    decision = _gate({"gate": 0, "kind": "approve_plan",
                      "channel": state.get("origin_channel"), "brand": brand})
    if decision.get("decision") == "approved":
        await _agent(state, "WebSearch", "started",
                     f"Plan approved — WebSearch started for {brand} + {len(competitors)} "
                     f"competitors across {label}.")
        return {"plan_decision": "approved"}
    with contextlib.suppress(Exception):
        await _apply_plan_changes(session_id, decision.get("feedback", ""))
    return {"plan_decision": "changes", "plan_feedback": decision.get("feedback", "")}


def _ref_footer(token: str) -> str:
    if not token:
        return ""
    return (f'<div style="max-width:640px;margin:8px auto 0;color:#a7a19a;font-size:11px;'
            f'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif">'
            f"Reference (keep in your reply): {token}</div>")


# ------------------------------------------------------------------ Stage 2: collect + gate
async def collect(state: PipelineState) -> dict:
    from app.services import ingestion_service

    session_id = state["session_id"]
    config = await _session_config(session_id)
    brand = config.get("brand", state.get("brand", ""))
    await _progress(state, f"🔍 Searching all sources for {brand} + competitors…")
    stats = await ingestion_service.collect(
        session_id=session_id, brand=brand,
        query_groups=config.get("query_groups", state.get("query_groups", [])),
        days_back=config.get("days_back"),
        run_id=state.get("run_id"),
    )
    return {
        "raw_count": stats["raw"], "unique_count": stats["unique"],
        "source_file_key": keys.source_file(session_id),
        "notes": {"collect": stats["per_source"]},
    }


async def enrich(state: PipelineState) -> dict:
    from app.agents.source_enricher import enrich_articles
    from app.tools.connectors.base import RawArticle

    store = get_artifact_store()
    session_id = state["session_id"]
    await _progress(state, f"🌐 Resolving publisher country/author for "
                           f"{state.get('unique_count', 0)} articles…")
    payload = await store.get_json(keys.source_file(session_id))
    articles = [RawArticle(**a) for a in payload.get("articles", [])]
    stats = await enrich_articles(articles, state["project_id"])
    payload["articles"] = [a.model_dump(mode="json") for a in articles]
    await store.put_json(keys.source_file(session_id), payload)
    return {"enriched_count": stats["countries_resolved"], "notes": {"enrich": stats}}


async def _export_gate_csv(session_id: str, gate: int) -> tuple[str, str]:
    store = get_artifact_store()
    if gate == 1:
        payload = await store.get_json(keys.source_file(session_id))
        data = articles_to_csv(payload.get("articles", []), COLLECTED_COLUMNS)
    else:
        payload = await store.get_json(keys.tagged_file(session_id))
        data = articles_to_csv(payload.get("articles", []), TAGGED_COLUMNS)
    key = keys.gate_csv(session_id, gate)
    ref = await store.put_bytes(key, data, "text/csv")
    return key, ref.sha256


async def _notify_gate(state: PipelineState, gate: int, csv_key: str, csv_sha: str,
                       message: str, html: str = "",
                       extra_attachments: list | None = None) -> None:
    try:
        from app.channels.notifier import notify_gate

        await notify_gate(state, gate=gate, csv_key=csv_key, csv_sha=csv_sha,
                          message=message, html=html, extra_attachments=extra_attachments)
    except Exception as exc:
        log.info("gate.notify_skipped", gate=gate, reason=str(exc)[:120])


async def collect_gate(state: PipelineState) -> dict:
    from app.analytics import stage_stats
    from app.channels import stage_report
    from app.security.auth import resume_token

    session_id = state["session_id"]
    config = await _session_config(session_id)
    brand = config.get("brand", state.get("brand", "Brand"))
    competitors = config.get("competitors", [])
    csv_key, csv_sha = await _export_gate_csv(session_id, 1)
    token = resume_token(state.get("run_id", ""))

    html_body, extra = "", []
    import contextlib
    with contextlib.suppress(Exception):
        articles = (await get_artifact_store().get_json(
            keys.source_file(session_id))).get("articles", [])
        stats = stage_stats.collection_stats(articles, brand=brand, competitors=competitors)
        html_body = stage_report.collection_kpi_html(
            _tid(state), brand, stats,
            tagging_sources="Google News, SearXNG, DuckDuckGo, social (Reddit/TikTok), "
            "plus any keyed connectors",
            enrichment_points="publisher country, author/byline, monthly reach") + _ref_footer(token)
        snap = stage_report.echarts_snapshot(f"{brand} — Collection", [
            {"id": "grp", "title": "Coverage by group", "type": "bar",
             "categories": [k for k, _ in stats["group_series"]],
             "values": [v for _, v in stats["group_series"]]},
            {"id": "subj", "title": "By brand & competitor", "type": "bar",
             "categories": [k for k, _ in stats["subject_series"]],
             "values": [v for _, v in stats["subject_series"]]}])
        extra = [("stage2_collection.html", snap, "text/html")]

    message = ("Collected coverage is ready (CSV attached). Reply APPROVE to tag, "
               "'add: <data point>' to include more, or 'change: …'. "
               f"Reference: {token}")
    await _notify_gate(state, 1, csv_key, csv_sha, message, html=html_body,
                       extra_attachments=extra)

    decision = _gate({"gate": 1, "kind": "approve_collection", "csv_key": csv_key,
                      "channel": state.get("origin_channel"), "message": message})
    result = {"gate1_decision": decision.get("decision"),
              "gate1_feedback": decision.get("feedback", "")}
    if decision.get("decision") == "approved":
        adds = _parse_tagging_additions(decision.get("feedback", ""))
        if adds:
            with contextlib.suppress(Exception):
                await _persist_tagging_additions(session_id, state["project_id"], adds)
            result["tagging_additions"] = adds
            await _progress(state, f"🧠 Tagging memory updated — will also tag: "
                                   f"{', '.join(adds)}.")
    return result


_ADD_RE = re.compile(r"(?:^|\b)(?:add|include|also (?:tag|track)|capture)[: ]+([^\n.;]+)", re.I)


def _parse_tagging_additions(feedback: str) -> list[str]:
    out: list[str] = []
    for m in _ADD_RE.finditer(feedback or ""):
        for piece in re.split(r",| and ", m.group(1)):
            p = piece.strip().strip(".").strip()
            # drop plan-style competitor adds; those are handled in the plan gate
            if p and "competitor" not in p.lower() and len(p) < 60 and p not in out:
                out.append(p)
    return out[:8]


async def _persist_tagging_additions(session_id: str, project_id: str, adds: list[str]) -> None:
    """Apply to this run (session config) AND persist to the project so future runs inherit."""
    from app.db.base import get_sessionmaker
    from app.db.models import Project
    from app.db.models import Session as SessionRow

    async with get_sessionmaker()() as db, db.begin():
        srow = await db.get(SessionRow, uuid.UUID(session_id))
        if srow is not None:
            cfg = dict(srow.config or {})
            cfg["tagging_additions"] = list(dict.fromkeys(
                [*cfg.get("tagging_additions", []), *adds]))
            srow.config = cfg
        prow = await db.get(Project, uuid.UUID(str(project_id)))
        if prow is not None:
            prow.tagging_notes = list(dict.fromkeys([*(prow.tagging_notes or []), *adds]))


# ------------------------------------------------------------------ Stage 3: tag + gate
async def tag(state: PipelineState) -> dict:
    from app.services.tagging_service import tag_session

    await _agent(state, "Tagging", "started",
                 f"Tagging {state.get('unique_count', 0)} articles: sentiment "
                 "(+confidence & reason), theme tiers, emotions, signals, entities, section.")
    await _progress(state, "🏷️ Tagging articles…")
    stats = await tag_session(session_id=state["session_id"], project_id=state["project_id"])
    return {
        "tagged_count": stats["tagged"],
        "tagged_file_key": keys.tagged_file(state["session_id"]),
        "notes": {"tag": {k: v for k, v in stats.items() if k != "failed_batches"}},
    }


async def tagged_gate(state: PipelineState) -> dict:
    from app.analytics import stage_stats
    from app.channels import stage_report
    from app.security.auth import resume_token

    session_id = state["session_id"]
    config = await _session_config(session_id)
    brand = config.get("brand", state.get("brand", "Brand"))
    competitors = config.get("competitors", [])
    csv_key, csv_sha = await _export_gate_csv(session_id, 2)
    token = resume_token(state.get("run_id", ""))

    html_body, extra = "", []
    import contextlib
    with contextlib.suppress(Exception):
        articles = (await get_artifact_store().get_json(
            keys.tagged_file(session_id))).get("articles", [])
        stats = stage_stats.tagging_stats(articles)
        breakdown = stage_stats.brand_breakdown(articles, brand=brand, competitors=competitors)
        collected = state.get("unique_count") or len(articles)
        dropped = max(0, collected - stats["tagged"])   # collected but not tagged
        html_body = stage_report.tagged_results_html(
            _tid(state), brand, stats, breakdown, collected=collected, dropped=dropped,
            memory_updates=config.get("tagging_additions", [])) + _ref_footer(token)
        snap = stage_report.echarts_snapshot(f"{brand} — Tagging", [
            {"id": "thm", "title": "Top themes", "type": "bar",
             "categories": [t for t, _ in stats["top_themes"]],
             "values": [v for _, v in stats["top_themes"]]},
            {"id": "sov", "title": "Share of voice", "type": "bar",
             "categories": [n for n, _ in breakdown["sov_series"]],
             "values": [v for _, v in breakdown["sov_series"]]}])
        extra = [("stage3_tagging.html", snap, "text/html")]

    message = ("Tagged articles are ready to sign off (CSV attached). Reply APPROVE to build "
               "the dashboard, or reply with an edited CSV setting Monitoring=FALSE on rows "
               f"to exclude. Reference: {token}")
    await _notify_gate(state, 2, csv_key, csv_sha, message, html=html_body,
                       extra_attachments=extra)

    decision = _gate({"gate": 2, "kind": "approve_tagged", "csv_key": csv_key,
                      "channel": state.get("origin_channel"), "message": message})
    result = {"gate2_decision": decision.get("decision"),
              "gate2_feedback": decision.get("feedback", "")}
    if decision.get("decision") == "approved":
        from app.services.review_service import bulk_approve
        with contextlib.suppress(Exception):
            n = await bulk_approve(project_id=state["project_id"], session_id=session_id)
            result["approved_count"] = n
            csv_ovr = decision.get("monitoring_csv_key")
            if csv_ovr:
                from app.services.review_service import apply_monitoring_csv

                data = await get_artifact_store().get_bytes(csv_ovr)
                counts = await apply_monitoring_csv(
                    project_id=state["project_id"], session_id=session_id, csv_bytes=data)
                result["monitoring_dropped"] = counts["dropped"]
                await _progress(state, f"🗂️ Monitoring updated — {counts['dropped']} excluded, "
                                       f"{counts['kept']} kept. Building dashboard.")
            else:
                await _progress(state, f"✅ Signed off {n} articles — building the dashboard.")
    return result


# ------------------------------------------------------------------ dashboards / deliver
async def dashboards(state: PipelineState) -> dict:
    import contextlib

    from app.services.dashboard_service import build_dashboards

    await _agent(state, "Dashboard", "started",
                 "Approved — building your dashboards, per-chart insights and the "
                 "branded report now.")
    await _progress(state, "📊 Building the dashboard and branded report…")
    payload = await build_dashboards(session_id=state["session_id"], force=True)

    html_built = False
    with contextlib.suppress(Exception):
        from app.agents.dashboard_agent import DashboardRequest, build_schema
        from app.services.html_renderer import render

        config = await _session_config(state["session_id"])
        schema = await build_schema(DashboardRequest(
            title=f"{config.get('brand', state.get('brand', 'Brand'))} — Media Intelligence",
            project_id=state["project_id"],
            session_id=state["session_id"],
            feedback_context=state.get("gate2_feedback", ""),
        ))
        html = render(schema)
        await get_artifact_store().put_bytes(
            f"reports/{state['session_id']}/dashboard.html", html.encode(), "text/html")
        html_built = True

    return {
        "approved_count": payload["approved_count"],
        "monitoring_count": payload["monitoring_count"],
        "charts_data_file_key": keys.charts_data_file(state["session_id"]),
        "notes": {"dashboards": {"approved": payload["approved_count"],
                                 "monitoring": payload["monitoring_count"],
                                 "html_built": html_built}},
    }


async def reflect(state: PipelineState) -> dict:
    import contextlib

    from app.memory.correction_memory import distill_to_mem0
    from app.memory.mem0_service import MemoryType, remember

    distilled = 0
    with contextlib.suppress(Exception):
        distilled = await distill_to_mem0(state["project_id"], run_id=state.get("run_id"))
    with contextlib.suppress(Exception):
        await remember(
            agent="reflection", memory_type=MemoryType.EPISODIC,
            project_id=state["project_id"], run_id=state.get("run_id"),
            content=(
                f"Run {state.get('run_id', '?')} for session {state['session_id']}: "
                f"{state.get('unique_count', 0)} collected, {state.get('tagged_count', 0)} "
                f"tagged, {state.get('approved_count', 0)} approved; "
                f"g1={state.get('gate1_feedback', '')!r} g2={state.get('gate2_feedback', '')!r}"
            ),
        )
    return {"notes": {"reflect": {"feedback_rules_distilled": distilled}}}


async def deliver(state: PipelineState) -> dict:
    import contextlib

    dashboard_url = None
    with contextlib.suppress(Exception):
        from app.services.vercel_publisher import publish_from_artifact

        dashboard_url = await publish_from_artifact(
            state["session_id"], state.get("brand", "report"), state.get("task_id", ""))
    if dashboard_url:
        state["dashboard_url"] = dashboard_url

    # persist the report to the run row so it's listable/retrievable later (archive)
    with contextlib.suppress(Exception):
        from app.db.base import get_sessionmaker
        from app.db.models import Run

        async with get_sessionmaker()() as db, db.begin():
            run = await db.get(Run, uuid.UUID(str(state["run_id"])))
            if run is not None:
                addr = dict(run.origin_address or {})
                addr.update(dashboard_url=dashboard_url or addr.get("dashboard_url"),
                            brand=state.get("brand"),
                            monitoring_count=state.get("monitoring_count"))
                run.origin_address = addr

    with contextlib.suppress(Exception):
        from app.channels.notifier import notify_complete

        await notify_complete(state)
    return {"notes": {"deliver": {"channel": state.get("origin_channel"),
                                  "dashboard_url": dashboard_url}}}


# ------------------------------------------------------------------ routing / build
def _route_plan(state: PipelineState) -> str:
    return "collect" if state.get("plan_decision") == "approved" else "plan_gate"


def _route_collect(state: PipelineState) -> str:
    return "tag" if state.get("gate1_decision") == "approved" else "collect"


def _route_tagged(state: PipelineState) -> str:
    return "dashboards" if state.get("gate2_decision") == "approved" else "tag"


def build_pipeline_graph(checkpointer=None):
    g = StateGraph(PipelineState)
    g.add_node("plan_gate", plan_gate)
    g.add_node("collect", collect)
    g.add_node("enrich", enrich)
    g.add_node("collect_gate", collect_gate)
    g.add_node("tag", tag)
    g.add_node("tagged_gate", tagged_gate)
    g.add_node("dashboards", dashboards)
    g.add_node("reflect", reflect)
    g.add_node("deliver", deliver)

    g.add_edge(START, "plan_gate")
    g.add_conditional_edges("plan_gate", _route_plan,
                            {"collect": "collect", "plan_gate": "plan_gate"})
    g.add_edge("collect", "enrich")
    g.add_edge("enrich", "collect_gate")
    g.add_conditional_edges("collect_gate", _route_collect,
                            {"tag": "tag", "collect": "collect"})
    g.add_edge("tag", "tagged_gate")
    g.add_conditional_edges("tagged_gate", _route_tagged,
                            {"dashboards": "dashboards", "tag": "tag"})
    g.add_edge("dashboards", "reflect")
    g.add_edge("reflect", "deliver")
    g.add_edge("deliver", END)

    return g.compile(checkpointer=checkpointer)


graph = build_pipeline_graph()
