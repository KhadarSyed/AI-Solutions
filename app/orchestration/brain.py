"""Brain router — classifies each user query to a retrieval/answer strategy.

Routes: MEMORY (answer from Mem0), RAG (approved-corpus retrieval), WEB (fresh
web search + scrape), LLM (general knowledge/formatting). Hybrids allowed."""

from enum import StrEnum

from pydantic import BaseModel, Field

from app.llm_gateway.guarded import GuardedAgent


class Route(StrEnum):
    MEMORY = "MEMORY"
    RAG = "RAG"
    WEB = "WEB"
    LLM = "LLM"


class RouteDecision(BaseModel):
    routes: list[Route] = Field(
        description="One or more routes, in execution order. Most queries need exactly one."
    )
    reason: str = Field(description="One short sentence: why these routes")
    confidence: float = Field(ge=0, le=1)
    search_query: str = Field(
        default="", description="Refined query for RAG/WEB retrieval (empty for MEMORY/LLM)"
    )


_SYSTEM = """You route PR-analyst questions to the right strategy.
- MEMORY: asks what was decided/learned/preferred before ("what did we decide about X",
  "which competitors do we track", "what's our house style"). Answer from agent memory.
- RAG: asks about THIS project's collected coverage ("what are people saying about our
  pricing", "summarize CEO coverage"). Retrieve from the approved article corpus.
- WEB: asks for fresh/current info not in the corpus ("latest news on X", "what happened
  today", competitor facts we haven't collected). Search the live web.
- LLM: general knowledge, definitions, formatting, or rewrites needing no retrieval.
Prefer a single route. Combine (e.g. RAG+WEB) only when the question genuinely needs both.
Set search_query to a focused retrieval query when routes include RAG or WEB."""


def brain_agent() -> GuardedAgent[RouteDecision]:
    return GuardedAgent(
        purpose="brain_route",
        stage="brain",
        system_prompt=_SYSTEM,
        output_type=RouteDecision,
        temperature=0.0,
        user_originated_input=True,
        cacheable=True,
    )


async def route(query: str, *, request_id: str | None = None) -> RouteDecision:
    try:
        decision = await brain_agent().run(query, request_id=request_id)
        if not decision.routes:
            decision.routes = [Route.RAG]
        if not decision.search_query and (Route.RAG in decision.routes or Route.WEB in decision.routes):
            decision.search_query = query
        return decision
    except Exception:
        # safe default: ground in the approved corpus
        return RouteDecision(routes=[Route.RAG], reason="router fallback",
                             confidence=0.3, search_query=query)
