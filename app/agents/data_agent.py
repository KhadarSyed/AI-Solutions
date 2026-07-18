"""Stage 6 — interactive data agent.

An intent classifier splits each message into a CHART request (write pandas/
matplotlib, run in the sandbox, up to 3 regenerations on code error) or a
QUESTION (Brain-routed: MEMORY / RAG / WEB / LLM, grounded). Everything streams
as typed events."""

from collections.abc import AsyncIterator
from enum import StrEnum

from pydantic import BaseModel, Field

from app.artifacts import keys
from app.artifacts.factory import get_artifact_store
from app.llm_gateway.guarded import GuardedAgent
from app.observability.logging import get_logger
from app.orchestration import brain
from app.retrieval.retriever import retrieve, to_context_block
from app.tools.sandbox.base import ErrorKind
from app.tools.sandbox.factory import get_sandbox

log = get_logger(__name__)

MAX_CODE_REGENERATIONS = 3


class Intent(StrEnum):
    CHART = "chart"
    QUESTION = "question"


class IntentDecision(BaseModel):
    intent: Intent
    reason: str = Field(description="one short sentence")


class ChartCode(BaseModel):
    code: str = Field(
        description="Python using pandas as pd and matplotlib; the articles are preloaded "
        "as a DataFrame `df`. Save the figure to JOB_DIR/chart.png and set `result` to a "
        "small dict describing what was plotted. No network, no file reads outside JOB_DIR."
    )
    title: str = Field(description="Chart title to show the user")


async def _classify_intent(message: str, request_id: str) -> IntentDecision:
    agent = GuardedAgent(
        purpose="data_agent_intent", stage="data_agent",
        system_prompt=(
            "Classify the analyst's message: CHART if they want a plot/graph/visualization "
            "computed from the article data; QUESTION otherwise."
        ),
        output_type=IntentDecision, temperature=0.0, user_originated_input=True,
    )
    try:
        return await agent.run(message, request_id=request_id)
    except Exception:
        return IntentDecision(intent=Intent.QUESTION, reason="intent fallback")


async def _articles_df_bytes(session_id: str) -> bytes:
    """Approved tagged articles as a parquet the sandbox loads into `df`."""
    import io

    import pandas as pd

    tagged = await get_artifact_store().get_json(keys.tagged_file(session_id))
    approved = [a for a in tagged.get("articles", []) if a.get("is_approved")]
    frame = pd.DataFrame(approved or tagged.get("articles", []))
    buf = io.BytesIO()
    frame.to_parquet(buf, index=False)
    return buf.getvalue()


_CHART_PREAMBLE = (
    "import os, pandas as pd, matplotlib\n"
    "matplotlib.use('Agg')\nimport matplotlib.pyplot as plt\n"
    "df = pd.read_parquet(os.path.join(JOB_DIR, 'articles.parquet'))\n"
)


async def _chart_flow(session_id: str, message: str, request_id: str) -> AsyncIterator[dict]:
    yield {"event": "plan", "data": {"message": "Writing analysis code for your chart."}}

    coder = GuardedAgent(
        purpose="data_agent_code", stage="data_agent",
        system_prompt=(
            "Write Python that analyzes the preloaded `df` (approved tagged articles) and "
            "produces ONE matplotlib chart saved to os.path.join(JOB_DIR,'chart.png') "
            "(dpi=140, tight_layout). Set `result` to a short dict summary. `df`, `JOB_DIR`, "
            "pd, plt are available. No imports beyond pandas/matplotlib/numpy; no network/file IO."
        ),
        output_type=ChartCode, temperature=0.1,
    )

    df_bytes = await _articles_df_bytes(session_id)
    sandbox = get_sandbox()
    feedback = ""
    for attempt in range(1, MAX_CODE_REGENERATIONS + 1):
        prompt = f"Request: {message}"
        if feedback:
            prompt += f"\n\nYour previous code failed with:\n{feedback}\nFix it."
        try:
            spec = await coder.run(prompt, request_id=request_id)
        except Exception as exc:
            yield {"event": "error", "data": {"message": f"code generation failed: {exc}"}}
            return

        full_code = _CHART_PREAMBLE + spec.code
        yield {"event": "code", "data": {"attempt": attempt, "title": spec.title,
                                         "code": spec.code[:4000]}}

        result = await sandbox.run(full_code, {"articles.parquet": df_bytes}, timeout=30)
        if result.ok:
            png = result.files.get("chart.png")
            yield {"event": "chart", "data": {
                "title": spec.title, "summary": result.result_json,
                "image_base64": png, "attempts": attempt,
            }}
            return
        if result.error_kind == ErrorKind.INFRA:
            yield {"event": "error", "data": {"message": f"sandbox unavailable: {result.stderr[:300]}",
                                              "retryable": False}}
            return
        feedback = result.stderr
        yield {"event": "code_error", "data": {"attempt": attempt, "traceback": result.stderr[:800]}}

    yield {"event": "error", "data": {"message": "Could not produce a working chart after "
                                      f"{MAX_CODE_REGENERATIONS} attempts.", "retryable": False}}


async def _question_flow(
    project_id: str, session_id: str, message: str, request_id: str
) -> AsyncIterator[dict]:
    decision = await brain.route(message, request_id=request_id)
    yield {"event": "intent", "data": {"routes": [r.value for r in decision.routes],
                                       "reason": decision.reason,
                                       "confidence": decision.confidence}}

    context_parts: list[str] = []
    citations: list[dict] = []

    if brain.Route.MEMORY in decision.routes:
        from app.memory.mem0_service import recall

        try:
            hits = await recall(agent="data_agent", project_id=project_id,
                                query=decision.search_query or message, limit=8)
            context_parts.append("From memory:\n" + "\n".join(
                f"- {h.get('memory', h)}" for h in hits))
        except Exception:
            pass

    if brain.Route.RAG in decision.routes:
        articles = await retrieve(project_id=project_id, query=decision.search_query or message,
                                  session_id=session_id, approved_only=True)
        context_parts.append(to_context_block(articles))
        citations = [{"id": a.article_id, "score": round(a.rerank_score, 3),
                      "publisher": a.meta.get("publisher", "")} for a in articles]
        yield {"event": "retrieval", "data": {"count": len(articles), "citations": citations}}

    if brain.Route.WEB in decision.routes:
        web = await _web_context(decision.search_query or message)
        if web:
            context_parts.append("From live web search:\n" + web)
            yield {"event": "web", "data": {"used": True}}

    answer_agent = GuardedAgent(
        purpose="data_agent_answer", stage="data_agent",
        system_prompt=(
            "Answer the analyst's question. Ground every claim in the provided context and "
            "cite article ids like [A12] when you use the corpus. If context is thin, say so "
            "rather than inventing facts."
        ),
        output_type=str, temperature=0.2,
        stakeholder_facing_output=True,
        require_citations=brain.Route.RAG in decision.routes,
    )
    context = "\n\n".join(p for p in context_parts if p) or "No supporting context found."
    try:
        answer = await answer_agent.run(
            f"Question: {message}\n\nContext:\n{context[:12000]}", request_id=request_id
        )
    except Exception as exc:
        yield {"event": "error", "data": {"message": f"answer failed: {exc}"}}
        return
    yield {"event": "answer", "data": {"text": answer, "citations": citations}}


async def _web_context(query: str, max_results: int = 5) -> str:
    import asyncio

    def _search() -> list[str]:
        from ddgs import DDGS

        with DDGS() as ddgs:
            return [f"{r.get('title', '')}: {r.get('body', '')}"
                    for r in ddgs.text(query, max_results=max_results)]

    try:
        return "\n".join(await asyncio.to_thread(_search))[:4000]
    except Exception:
        return ""


async def handle_message(
    *, project_id: str, session_id: str, message: str, request_id: str
) -> AsyncIterator[dict]:
    yield {"event": "start", "data": {"message": message}}
    intent = await _classify_intent(message, request_id)
    yield {"event": "intent_class", "data": {"intent": intent.intent.value,
                                             "reason": intent.reason}}
    if intent.intent == Intent.CHART:
        async for ev in _chart_flow(session_id, message, request_id):
            yield ev
    else:
        async for ev in _question_flow(project_id, session_id, message, request_id):
            yield ev
    yield {"event": "complete", "data": {}}
