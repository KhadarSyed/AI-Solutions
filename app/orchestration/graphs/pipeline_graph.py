"""The 7-stage pipeline graph with two channel-delivered human gates.

Nodes call the real stage services; both gates export a CSV artifact, notify the
origin channel (Notifier — Phase D adapters; web origin streams the event), and
interrupt until the channel answers. Studio loads the module-level `graph`;
RunManager compiles with the Postgres checkpointer.
"""

import uuid

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.channels.csv_export import COLLECTED_COLUMNS, TAGGED_COLUMNS, articles_to_csv
from app.observability.logging import get_logger
from app.orchestration.state import PipelineState

log = get_logger(__name__)


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


async def collect(state: PipelineState) -> dict:
    from app.services import ingestion_service

    session_id = state["session_id"]
    brand = state.get("brand", "the brand")
    config = await _session_config(session_id)
    competitors = config.get("competitors", state.get("competitors", []))
    comp_txt = f" Competitors: {', '.join(competitors)}." if competitors else ""
    await _agent(state, "WebSearch", "started",
                 f"Stage 1/3 — collecting {brand} + competitor + industry coverage from all "
                 f"sources.{comp_txt}")
    await _progress(state, f"🔍 Searching news sources for {brand} (last 48 hours)…")
    stats = await ingestion_service.collect(
        session_id=session_id,
        brand=config.get("brand", state.get("brand", "")),
        query_groups=config.get("query_groups", state.get("query_groups", [])),
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
    """Send the gate CSV + styled staged update to the run's origin channel (adapters land
    in Phase D; web-origin runs observe the awaiting_human event on /ws/runs)."""
    try:
        from app.channels.notifier import notify_gate

        await notify_gate(state, gate=gate, csv_key=csv_key, csv_sha=csv_sha,
                          message=message, html=html, extra_attachments=extra_attachments)
    except Exception as exc:
        log.info("gate.notify_skipped", gate=gate, reason=str(exc)[:120])


def _with_ref(html_body: str, token: str) -> str:
    """Embed the reply reference in the styled body so it survives quoted replies."""
    if not html_body or not token:
        return html_body
    footer = (f'<div style="max-width:660px;margin:8px auto 0;color:#6b6b70;font-size:11px;'
              f'font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;">'
              f"Reference (keep in your reply): {token}</div>")
    return html_body + footer


async def _stage_pack(state: PipelineState, gate: int) -> tuple[str, list]:
    """Build the CSS-styled staged HTML body + an ECharts snapshot attachment for a gate.
    Every number is computed deterministically by stage_stats. Best-effort — returns
    ('', []) on any failure so a gate never blocks on presentation."""
    import contextlib

    html_body, attachments = "", []
    with contextlib.suppress(Exception):
        from app.analytics import stage_stats
        from app.channels import stage_report

        session_id = state["session_id"]
        config = await _session_config(session_id)
        brand = config.get("brand", state.get("brand", "Brand"))
        competitors = config.get("competitors", [])
        tid = state.get("task_id") or (state.get("origin_address") or {}).get("task_id") or ""
        store = get_artifact_store()

        if gate == 1:
            articles = (await store.get_json(keys.source_file(session_id))).get("articles", [])
            stats = stage_stats.collection_stats(articles, brand=brand, competitors=competitors)
            html_body = stage_report.collection_html(tid, brand, 3, stats)
            snap = stage_report.echarts_snapshot(f"{brand} — Collection", [
                {"id": "grp", "title": "Coverage by group", "type": "bar",
                 "categories": [k for k, _ in stats["group_series"]],
                 "values": [v for _, v in stats["group_series"]]},
                {"id": "subj", "title": "By brand & competitor", "type": "bar",
                 "categories": [k for k, _ in stats["subject_series"]],
                 "values": [v for _, v in stats["subject_series"]]},
            ])
            attachments = [("stage1_summary.html", snap, "text/html")]
        else:
            articles = (await store.get_json(keys.tagged_file(session_id))).get("articles", [])
            stats = stage_stats.tagging_stats(articles)
            html_body = stage_report.tagging_html(tid, brand, 3, stats)
            snap = stage_report.echarts_snapshot(f"{brand} — Tagging", [
                {"id": "vol", "title": "Volume over time", "type": "line",
                 "categories": [d for d, _ in stats["volume_series"]],
                 "values": [v for _, v in stats["volume_series"]]},
                {"id": "thm", "title": "Top themes", "type": "bar",
                 "categories": [t for t, _ in stats["top_themes"]],
                 "values": [v for _, v in stats["top_themes"]]},
            ])
            attachments = [("stage2_summary.html", snap, "text/html")]
    return html_body, attachments


async def gate1_consent(state: PipelineState) -> dict:
    from app.security.auth import resume_token

    session_id = state["session_id"]
    csv_key, csv_sha = await _export_gate_csv(session_id, 1)
    token = resume_token(state.get("run_id", ""))
    message = ("Collected articles are ready (CSV attached). "
               "Reply APPROVE to start enrichment & tagging, or CHANGES with instructions. "
               f"Please keep this reference in your reply: {token}")
    html_body, extra = await _stage_pack(state, 1)
    html_body = _with_ref(html_body, token)
    await _notify_gate(state, 1, csv_key, csv_sha, message, html=html_body,
                       extra_attachments=extra)
    decision = interrupt({
        "gate": 1, "kind": "consent_to_enrich", "csv_key": csv_key,
        "channel": state.get("origin_channel"), "message": message,
    })
    return {"gate1_decision": decision.get("decision"),
            "gate1_feedback": decision.get("feedback", "")}


async def tag(state: PipelineState) -> dict:
    from app.services.tagging_service import tag_session

    await _agent(state, "Tagging", "started",
                 f"Stage 2/3 — approved. Tagging {state.get('unique_count', 0)} articles: "
                 "sentiment (+confidence & reason), theme tiers, emotions, signals, entities "
                 "and section. WebSearch Agent is free for your next request.")
    await _progress(state, "🏷️ Tagging articles (sentiment, theme, section, entities)…")
    stats = await tag_session(session_id=state["session_id"], project_id=state["project_id"])
    return {
        "tagged_count": stats["tagged"],
        "tagged_file_key": keys.tagged_file(state["session_id"]),
        "notes": {"tag": {k: v for k, v in stats.items() if k != "failed_batches"}},
    }


async def gate2_approval(state: PipelineState) -> dict:
    from app.security.auth import resume_token

    session_id = state["session_id"]
    csv_key, csv_sha = await _export_gate_csv(session_id, 2)
    token = resume_token(state.get("run_id", ""))
    message = ("Tagged articles are ready for your approval (CSV attached). "
               "Reply APPROVE to build dashboards, or reply with an edited CSV to set "
               "Monitoring=FALSE on rows to exclude. "
               f"Please keep this reference in your reply: {token}")
    html_body, extra = await _stage_pack(state, 2)
    html_body = _with_ref(html_body, token)
    await _notify_gate(state, 2, csv_key, csv_sha, message, html=html_body,
                       extra_attachments=extra)
    decision = interrupt({
        "gate": 2, "kind": "approve_tagged", "csv_key": csv_key,
        "channel": state.get("origin_channel"), "message": message,
    })
    result = {"gate2_decision": decision.get("decision"),
              "gate2_feedback": decision.get("feedback", "")}
    # Approving the tagged CSV means the stakeholder approved these articles —
    # mark the whole set approved so the dashboards/report (approved-only) aren't empty.
    if decision.get("decision") == "approved":
        import contextlib

        from app.services.review_service import bulk_approve
        with contextlib.suppress(Exception):
            n = await bulk_approve(project_id=state["project_id"], session_id=session_id)
            result["approved_count"] = n
            # Monitoring opt-out: an edited CSV attached to the approval can drop rows
            # (Monitoring=FALSE) from the monitoring set — only TRUE rows hit the dashboard.
            csv_key = decision.get("monitoring_csv_key")
            if csv_key:
                from app.artifacts.factory import get_artifact_store
                from app.services.review_service import apply_monitoring_csv

                data = await get_artifact_store().get_bytes(csv_key)
                counts = await apply_monitoring_csv(
                    project_id=state["project_id"], session_id=session_id, csv_bytes=data)
                result["monitoring_dropped"] = counts["dropped"]
                await _progress(
                    state, f"🗂️ Monitoring set updated from your CSV — {counts['dropped']} "
                    f"excluded, {counts['kept']} kept. Building dashboards.")
            else:
                await _progress(state, f"✅ Approved {n} articles — building dashboards.")
    return result


async def dashboards(state: PipelineState) -> dict:
    import contextlib

    from app.services.dashboard_service import build_dashboards

    await _agent(state, "Dashboard", "started",
                 "Approved — building your dashboards, per-chart insights and the "
                 "branded report now.")
    await _progress(state, "📊 Building the 5 dashboards and the branded report…")
    payload = await build_dashboards(session_id=state["session_id"], force=True)

    # Dashboard Agent: schema (memory/graph-biased charts, liked template) → dashboard.html
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
            f"reports/{state['session_id']}/dashboard.html", html.encode(), "text/html"
        )
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
    """End-of-run reflection: distill corrections + store an episodic summary."""
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
                f"{state.get('unique_count', 0)} collected, {state.get('tagged_count', 0)} tagged, "
                f"{state.get('approved_count', 0)} approved; gate feedback: "
                f"g1={state.get('gate1_feedback', '')!r} g2={state.get('gate2_feedback', '')!r}"
            ),
        )
    return {"notes": {"reflect": {"feedback_rules_distilled": distilled}}}


async def deliver(state: PipelineState) -> dict:
    """Publish the dashboard to Vercel (if configured), then send the finished
    analysis back to the origin channel with the durable link."""
    import contextlib

    dashboard_url = None
    with contextlib.suppress(Exception):
        from app.services.vercel_publisher import publish_from_artifact

        dashboard_url = await publish_from_artifact(
            state["session_id"], state.get("brand", "report"), state.get("task_id", ""))
    if dashboard_url:
        state["dashboard_url"] = dashboard_url

    with contextlib.suppress(Exception):
        from app.channels.notifier import notify_complete

        await notify_complete(state)
    return {"notes": {"deliver": {"channel": state.get("origin_channel"),
                                  "dashboard_url": dashboard_url}}}


def _route_gate1(state: PipelineState) -> str:
    return "tag" if state.get("gate1_decision") == "approved" else "collect"


def _route_gate2(state: PipelineState) -> str:
    return "dashboards" if state.get("gate2_decision") == "approved" else "tag"


def build_pipeline_graph(checkpointer=None):
    g = StateGraph(PipelineState)
    g.add_node("collect", collect)
    g.add_node("enrich", enrich)
    g.add_node("gate1_consent", gate1_consent)
    g.add_node("tag", tag)
    g.add_node("gate2_approval", gate2_approval)
    g.add_node("dashboards", dashboards)
    g.add_node("reflect", reflect)
    g.add_node("deliver", deliver)

    g.add_edge(START, "collect")
    g.add_edge("collect", "enrich")
    g.add_edge("enrich", "gate1_consent")
    g.add_conditional_edges("gate1_consent", _route_gate1, {"tag": "tag", "collect": "collect"})
    g.add_edge("tag", "gate2_approval")
    g.add_conditional_edges(
        "gate2_approval", _route_gate2, {"dashboards": "dashboards", "tag": "tag"}
    )
    g.add_edge("dashboards", "reflect")
    g.add_edge("reflect", "deliver")
    g.add_edge("deliver", END)

    return g.compile(checkpointer=checkpointer)


graph = build_pipeline_graph()
