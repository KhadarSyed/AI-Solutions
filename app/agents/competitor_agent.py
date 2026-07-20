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


async def _research(brand: str, project_id: str) -> list[str]:
    agent = GuardedAgent(
        purpose="competitor_research", stage="query_builder",
        system_prompt=(
            "Name the 5 most direct competitors of the given brand (same industry, "
            "comparable products). Return brand names only, most direct first."),
        output_type=_Competitors, temperature=0.0, cacheable=True,
    )
    res = await agent.run(f"Brand: {brand}")
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
