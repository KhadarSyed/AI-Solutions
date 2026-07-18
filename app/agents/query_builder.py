"""Stage 1 — conversational query configuration.

Four sub-stages (brand → queries → competitors → review). Each turn returns a
strict envelope; nothing persists until the user explicitly saves.
"""

import json

from pydantic import BaseModel, Field

from app.llm_gateway.guarded import GuardedAgent
from app.observability.logging import get_logger

log = get_logger(__name__)

SUBSTAGES = ("brand", "queries", "competitors", "review")


class QBState(BaseModel):
    sub_stage: str = "brand"
    brand: str = ""
    industry: str = ""
    query_groups: list[dict] = Field(default_factory=list)   # [{name, queries:[...]}]
    competitors: list[str] = Field(default_factory=list)
    suggested_competitors: list[str] = Field(default_factory=list)
    history: list[dict] = Field(default_factory=list)         # [{role, content}]


class QBTurn(BaseModel):
    message: str = Field(description="Conversational reply to show the user")
    brand: str = Field(default="", description="Brand if identified/confirmed this turn")
    industry: str = Field(default="", description="Industry if identified (never guessed)")
    query_groups: list[dict] = Field(
        default_factory=list,
        description='Boolean query groups like {"name": "Brand News", "queries": ["..."]}',
    )
    competitors: list[str] = Field(default_factory=list, description="Competitors user selected")
    substage_complete: bool = Field(default=False, description="This sub-stage is finished")
    options: list[str] = Field(default_factory=list, description="Quick-reply options for the user")


_PROMPTS = {
    "brand": (
        "You are the query-configuration agent for a PR monitoring platform. Sub-stage: BRAND. "
        "Identify and confirm the user's primary brand/company/product. If the industry is not "
        "obvious, ask ONE clarifying question — never guess the industry. Set substage_complete "
        "once brand (and industry) are confirmed."
    ),
    "queries": (
        "Sub-stage: QUERIES. Ask for topics to monitor, then generate grouped Boolean search "
        "queries (groups like Brand News / Competitors News / Industry News, or the user's own). "
        "If the user pastes a query spec, extract it verbatim into query_groups. Present the "
        "groups for confirmation; substage_complete when the user confirms."
    ),
    "competitors": (
        "Sub-stage: COMPETITORS. Real competitor candidates researched from the live web are "
        "provided in context. Offer them as options (max 8); the user multi-selects or adds "
        "their own. Never invent companies not in research or user input. substage_complete "
        "when selection is confirmed."
    ),
    "review": (
        "Sub-stage: REVIEW. Show a compact summary of brand, industry, query groups and "
        "competitors. The conversation never auto-ends: any field can be revised on request "
        "(apply revisions into the output fields). Remind the user to say 'save' to persist. "
        "Keep substage_complete false — only an explicit save action ends this stage."
    ),
}


async def research_competitors(brand: str, industry: str) -> list[str]:
    """Live web research for real competitors (DDG text search + extraction)."""
    import asyncio

    def _search() -> list[str]:
        from ddgs import DDGS

        snippets = []
        with DDGS() as ddgs:
            for r in ddgs.text(f"{brand} {industry} main competitors", max_results=6):
                snippets.append(f"{r.get('title', '')}: {r.get('body', '')}")
        return snippets

    try:
        snippets = await asyncio.to_thread(_search)
    except Exception as exc:
        log.warning("qb.competitor_research_failed", error=str(exc)[:120])
        return []

    class Extraction(BaseModel):
        competitors: list[str] = Field(description="Up to 8 real company names, no duplicates")

    extractor = GuardedAgent(
        purpose="competitor_research", stage="query_builder",
        system_prompt=(
            "Extract real competitor company names for the given brand from the search snippets. "
            "Only names actually present in the snippets. Max 8."
        ),
        output_type=Extraction, temperature=0.0, cacheable=True,
    )
    try:
        result = await extractor.run(
            f"Brand: {brand} (industry: {industry})\n\nSnippets:\n" + "\n".join(snippets)
        )
        return result.competitors[:8]
    except Exception:
        return []


def _turn_agent(sub_stage: str) -> GuardedAgent[QBTurn]:
    return GuardedAgent(
        purpose=f"query_builder_{sub_stage}",
        stage="query_builder",
        system_prompt=_PROMPTS[sub_stage],
        output_type=QBTurn,
        temperature=0.2,
        user_originated_input=True,
    )


async def run_turn(state: QBState, user_message: str) -> tuple[QBState, QBTurn]:
    context = {
        "brand": state.brand, "industry": state.industry,
        "query_groups": state.query_groups, "competitors": state.competitors,
        "suggested_competitors": state.suggested_competitors,
    }
    transcript = "\n".join(f"{m['role']}: {m['content']}" for m in state.history[-12:])
    prompt = (
        f"Current configuration:\n{json.dumps(context, ensure_ascii=False)}\n\n"
        f"Conversation so far:\n{transcript}\n\nuser: {user_message}"
    )

    turn = await _turn_agent(state.sub_stage).run(prompt)

    # fold extracted fields into state
    if turn.brand:
        state.brand = turn.brand
    if turn.industry:
        state.industry = turn.industry
    if turn.query_groups:
        state.query_groups = turn.query_groups
    if turn.competitors:
        state.competitors = turn.competitors
    state.history.append({"role": "user", "content": user_message})
    state.history.append({"role": "assistant", "content": turn.message})

    # sub-stage advancement + competitor research on entry
    if turn.substage_complete and state.sub_stage != "review":
        idx = SUBSTAGES.index(state.sub_stage)
        state.sub_stage = SUBSTAGES[idx + 1]
        if state.sub_stage == "competitors" and not state.suggested_competitors:
            state.suggested_competitors = await research_competitors(state.brand, state.industry)
            if state.suggested_competitors:
                turn.options = state.suggested_competitors

    return state, turn
