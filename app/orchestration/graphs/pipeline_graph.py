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


async def collect(state: PipelineState) -> dict:
    from app.services import ingestion_service

    session_id = state["session_id"]
    config = await _session_config(session_id)
    stats = await ingestion_service.collect(
        session_id=session_id,
        brand=config.get("brand", state.get("brand", "")),
        query_groups=config.get("query_groups", state.get("query_groups", [])),
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
                       message: str) -> None:
    """Send the gate CSV to the run's origin channel (adapters land in Phase D;
    web-origin runs observe the awaiting_human event on /ws/runs)."""
    try:
        from app.channels.notifier import notify_gate

        await notify_gate(state, gate=gate, csv_key=csv_key, csv_sha=csv_sha, message=message)
    except Exception as exc:
        log.info("gate.notify_skipped", gate=gate, reason=str(exc)[:120])


async def gate1_consent(state: PipelineState) -> dict:
    session_id = state["session_id"]
    csv_key, csv_sha = await _export_gate_csv(session_id, 1)
    message = ("Collected articles are ready (CSV attached). "
               "Reply APPROVE to start enrichment & tagging, or CHANGES with instructions.")
    await _notify_gate(state, 1, csv_key, csv_sha, message)
    decision = interrupt({
        "gate": 1, "kind": "consent_to_enrich", "csv_key": csv_key,
        "channel": state.get("origin_channel"), "message": message,
    })
    return {"gate1_decision": decision.get("decision"),
            "gate1_feedback": decision.get("feedback", "")}


async def tag(state: PipelineState) -> dict:
    from app.services.tagging_service import tag_session

    stats = await tag_session(session_id=state["session_id"], project_id=state["project_id"])
    return {
        "tagged_count": stats["tagged"],
        "tagged_file_key": keys.tagged_file(state["session_id"]),
        "notes": {"tag": {k: v for k, v in stats.items() if k != "failed_batches"}},
    }


async def gate2_approval(state: PipelineState) -> dict:
    session_id = state["session_id"]
    csv_key, csv_sha = await _export_gate_csv(session_id, 2)
    message = ("Tagged articles are ready for your approval (CSV attached). "
               "Reply APPROVE to build dashboards, or CHANGES with instructions. "
               "Detailed edits are available in the review console.")
    await _notify_gate(state, 2, csv_key, csv_sha, message)
    decision = interrupt({
        "gate": 2, "kind": "approve_tagged", "csv_key": csv_key,
        "channel": state.get("origin_channel"), "message": message,
    })
    return {"gate2_decision": decision.get("decision"),
            "gate2_feedback": decision.get("feedback", "")}


async def dashboards(state: PipelineState) -> dict:
    from app.services.dashboard_service import build_dashboards

    payload = await build_dashboards(session_id=state["session_id"], force=True)
    return {
        "approved_count": payload["approved_count"],
        "charts_data_file_key": keys.charts_data_file(state["session_id"]),
        "notes": {"dashboards": {"approved": payload["approved_count"],
                                 "monitoring": payload["monitoring_count"]}},
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

    g.add_edge(START, "collect")
    g.add_edge("collect", "enrich")
    g.add_edge("enrich", "gate1_consent")
    g.add_conditional_edges("gate1_consent", _route_gate1, {"tag": "tag", "collect": "collect"})
    g.add_edge("tag", "gate2_approval")
    g.add_conditional_edges(
        "gate2_approval", _route_gate2, {"dashboards": "dashboards", "tag": "tag"}
    )
    g.add_edge("dashboards", "reflect")
    g.add_edge("reflect", END)

    return g.compile(checkpointer=checkpointer)


graph = build_pipeline_graph()
