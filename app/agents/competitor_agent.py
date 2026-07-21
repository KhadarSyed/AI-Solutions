"""Resolve up to 5 competitors for a brand. Default: auto-research; skipped only
when the user explicitly names competitors. Caches research to Project.competitors
and Mem0 SEMANTIC (also seeds the previously-empty semantic memory type)."""
import contextlib
import uuid

from pydantic import BaseModel, Field
from sqlalchemy import update

from app.db.base import get_sessionmaker
from app.db.models import Project
from app.llm_gateway.guarded import GuardedAgent
from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)
MAX_COMPETITORS = 5


class _Competitors(BaseModel):
    competitors: list[str] = Field(
        description="direct competitor brand names, most direct first"
    )


class _Industry(BaseModel):
    industry: str = Field(
        description="the brand's primary industry/sector, specific enough to disambiguate "
                    "same-named companies (e.g. 'oncology & hematology biotech', not just "
                    "'healthcare'); a short phrase, no sentence")


async def _from_project(project_id: str) -> list[str]:
    async with get_sessionmaker()() as db:
        p = await db.get(Project, uuid.UUID(project_id))
        return list(p.competitors or []) if p else []


async def _from_memory(brand: str, project_id: str) -> list[str]:
    with contextlib.suppress(Exception):
        hits = await recall(agent="competitor", query=f"{brand} competitors",
                            project_id=project_id, memory_type=MemoryType.SEMANTIC, limit=1)
        for h in hits:
            names = (h.get("metadata") or {}).get("competitors")
            if names:
                return list(names)
    return []


async def _project_industry(project_id: str) -> str:
    with contextlib.suppress(Exception):
        async with get_sessionmaker()() as db:
            p = await db.get(Project, uuid.UUID(project_id))
            return (p.industry or "").strip() if p else ""
    return ""


async def _research_industry(brand: str) -> str:
    agent = GuardedAgent(
        purpose="industry_research", stage="query_builder",
        system_prompt=(
            "Identify the primary industry/sector of the given company or brand. Be specific "
            "enough to disambiguate same-named companies and to find true competitors "
            "(e.g. 'oncology & hematology biotechnology', 'HVAC & building climate systems', "
            "'multi-level-marketing consumer goods'). Return a short phrase only."),
        output_type=_Industry, temperature=0.0, cacheable=True,
    )
    res = await agent.run(f"Brand: {brand}")
    return (res.industry or "").strip()


async def ensure_industry(brand: str, project_id: str) -> str:
    """The project's industry, resolving + persisting it on first use when blank.

    This is what lets the agent run hands-off in production: a project created with no
    industry (auto-provisioned, or seeded before we knew it) self-heals — the industry is
    researched once, stored, and then drives correct competitor/entity disambiguation for
    this and every later run. No human ever sets it."""
    existing = await _project_industry(project_id)
    if existing:
        return existing
    try:
        industry = await _research_industry(brand)
    except Exception as exc:
        log.warning("industry.research_failed", brand=brand, error=str(exc)[:150])
        return ""
    if industry:
        with contextlib.suppress(Exception):
            async with get_sessionmaker()() as db, db.begin():
                await db.execute(update(Project).where(Project.id == uuid.UUID(project_id))
                                 .values(industry=industry))
        log.info("industry.resolved", brand=brand, industry=industry)
    return industry


async def _research(brand: str, project_id: str) -> list[str]:
    # The industry disambiguates same-named companies (e.g. "BeOne" the oncology
    # biotech vs. an identically-named consumer brand) so the model resolves the RIGHT
    # entity and its real rivals — no hard-coded competitor list. Resolve it if the
    # project doesn't have it yet, so competitor research is never entity-ambiguous.
    industry = await ensure_industry(brand, project_id)
    agent = GuardedAgent(
        purpose="competitor_research", stage="query_builder",
        system_prompt=(
            "You identify direct competitors. Given a brand and its industry, name the 5 "
            "most direct competitors — companies in the SAME industry with comparable "
            "products, competing for the same customers. Use the industry to resolve which "
            "specific company the brand refers to when the name is ambiguous. Return real "
            "company/brand names only, most direct first."),
        output_type=_Competitors, temperature=0.0, cacheable=True,
    )
    ctx = f"Brand: {brand}"
    if industry:
        ctx += f"\nIndustry: {industry}"
    res = await agent.run(ctx)
    return res.competitors


async def _cache(brand: str, project_id: str, names: list[str]) -> None:
    with contextlib.suppress(Exception):
        async with get_sessionmaker()() as db, db.begin():
            await db.execute(update(Project).where(Project.id == uuid.UUID(project_id))
                             .values(competitors=names))
    with contextlib.suppress(Exception):
        await remember(agent="competitor", memory_type=MemoryType.SEMANTIC,
                       project_id=project_id,
                       content=f"{brand} competitors: {', '.join(names)}",
                       metadata={"competitors": names})


async def resolve_competitors(brand: str, project_id: str,
                              override: list[str] | None = None) -> list[str]:
    if override:
        return override[:MAX_COMPETITORS]
    stored = await _from_project(project_id)
    if stored:
        return stored[:MAX_COMPETITORS]
    remembered = await _from_memory(brand, project_id)
    if remembered:
        return remembered[:MAX_COMPETITORS]
    try:
        names = (await _research(brand, project_id))[:MAX_COMPETITORS]
    except Exception as exc:
        log.warning("competitor.research_failed", brand=brand, error=str(exc)[:150])
        return []
    if names:
        await _cache(brand, project_id, names)
    return names
