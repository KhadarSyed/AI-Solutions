"""The 7-stage pipeline graph with two channel-delivered human gates.

Skeleton: every stage node exists and the two gates interrupt for channel
approval; stage bodies are wired to services incrementally in Phase B.
Studio loads the module-level `graph` (uncheckpointed — the runtime injects
persistence); RunManager compiles with the Postgres checkpointer.
"""

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from app.observability.logging import get_logger
from app.orchestration.state import PipelineState

log = get_logger(__name__)


async def collect(state: PipelineState) -> dict:
    log.info("pipeline.collect", session=state.get("session_id"))
    # Phase B: connector fleet fan-out → dedupe → relevancy filter → source_file
    return {"notes": {"collect": "stub"}}


async def enrich(state: PipelineState) -> dict:
    log.info("pipeline.enrich", session=state.get("session_id"))
    # Phase B: country/author resolution sub-agents
    return {"notes": {"enrich": "stub"}}


async def gate1_consent(state: PipelineState) -> dict:
    """Gate 1 — collected CSV to origin channel, hold for consent."""
    decision = interrupt(
        {
            "gate": 1,
            "kind": "consent_to_enrich",
            "csv_key": state.get("source_file_key"),
            "channel": state.get("origin_channel"),
            "message": "Collected articles are ready — approve in your channel to start tagging.",
        }
    )
    return {"gate1_decision": decision.get("decision"), "gate1_feedback": decision.get("feedback", "")}


async def tag(state: PipelineState) -> dict:
    log.info("pipeline.tag", session=state.get("session_id"))
    # Phase B: 18-field batch tagging with correction bias → tagged_file + embeddings
    return {"notes": {"tag": "stub"}}


async def gate2_approval(state: PipelineState) -> dict:
    """Gate 2 — tagged CSV to origin channel, hold for approval."""
    decision = interrupt(
        {
            "gate": 2,
            "kind": "approve_tagged",
            "csv_key": state.get("tagged_file_key"),
            "channel": state.get("origin_channel"),
            "message": "Tagged articles are ready — approve in your channel to build dashboards.",
        }
    )
    return {"gate2_decision": decision.get("decision"), "gate2_feedback": decision.get("feedback", "")}


async def dashboards(state: PipelineState) -> dict:
    log.info("pipeline.dashboards", session=state.get("session_id"))
    # Phase B: 5 deterministic chart builders + insight synthesis → charts_data_file
    return {"notes": {"dashboards": "stub"}}


async def reflect(state: PipelineState) -> dict:
    log.info("pipeline.reflect", session=state.get("session_id"))
    # Phase B/D: reflection agent writes run lessons to Mem0
    return {"notes": {"reflect": "stub"}}


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
