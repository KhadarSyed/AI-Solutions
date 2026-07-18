"""Unified memory API — Mem0 (pgvector vector store + Neo4j graph store).

Every agent stores/recalls through these helpers; each operation carries the
memory taxonomy type and fires the observability hook so the execution layer
can stream STORE/RECALL events per agent.
"""

from collections.abc import Awaitable, Callable
from enum import StrEnum
from functools import lru_cache
from urllib.parse import urlparse

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


class MemoryType(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    ASSOCIATIVE = "associative"   # lives in graph edges; type recorded on the source memory
    PROCEDURAL = "procedural"
    PROFILE = "profile"
    FEEDBACK = "feedback"
    RESEARCH = "research"


# observability hook — set by the execution layer; (op, agent, type, scope, summary)
MemoryEventHook = Callable[[str, str, str, str, str], Awaitable[None]]
_hook: MemoryEventHook | None = None


def set_memory_event_hook(hook: MemoryEventHook) -> None:
    global _hook
    _hook = hook


async def _emit(op: str, agent: str, mtype: str, scope: str, summary: str) -> None:
    if _hook is not None:
        try:
            await _hook(op, agent, mtype, scope, summary)
        except Exception as exc:  # observability must never break memory
            log.warning("memory.event_hook_failed", error=str(exc))


@lru_cache
def _memory():
    from mem0 import AsyncMemory  # heavy import — deferred

    s = get_settings()
    db = urlparse(s.database_url.replace("postgresql+asyncpg", "postgresql"))
    config = {
        "vector_store": {
            "provider": "pgvector",
            "config": {
                "user": db.username,
                "password": db.password,
                "host": db.hostname,
                "port": db.port or 5432,
                "dbname": db.path.lstrip("/"),
                "collection_name": "mem0_memories",
                "embedding_model_dims": 1536,
                "hnsw": True,
            },
        },
        "graph_store": {
            "provider": "neo4j",
            "config": {
                "url": s.neo4j_uri,
                "username": s.neo4j_username,
                "password": s.neo4j_password,
            },
        },
        "llm": {
            "provider": "azure_openai",
            "config": {
                "model": s.azure_openai_chat_deployment,
                "temperature": 0.0,
                "azure_kwargs": {
                    "api_key": s.azure_openai_api_key,
                    "azure_deployment": s.azure_openai_chat_deployment,
                    "azure_endpoint": s.azure_openai_endpoint,
                    "api_version": s.azure_openai_api_version,
                },
            },
        },
        "embedder": {
            "provider": "azure_openai",
            "config": {
                "model": s.azure_openai_embed_deployment,
                "azure_kwargs": {
                    "api_key": s.azure_openai_api_key,
                    "azure_deployment": s.azure_openai_embed_deployment,
                    "azure_endpoint": s.azure_openai_endpoint,
                    "api_version": s.azure_openai_api_version,
                },
            },
        },
    }
    return AsyncMemory.from_config(config)


def _scope(project_id: str) -> str:
    return f"project:{project_id}"


async def remember(
    *,
    agent: str,
    memory_type: MemoryType,
    content: str,
    project_id: str,
    user_id: str | None = None,
    run_id: str | None = None,
    metadata: dict | None = None,
) -> dict:
    """Store one memory, scoped to the project (and optionally a user)."""
    m = _memory()
    meta = {"type": memory_type.value, "agent": agent, "run_id": run_id, **(metadata or {})}
    result = await m.add(
        messages=[{"role": "user", "content": content}],
        user_id=_scope(project_id) if user_id is None else f"user:{user_id}",
        agent_id=agent,
        metadata={k: v for k, v in meta.items() if v is not None},
    )
    await _emit("STORE", agent, memory_type.value, _scope(project_id), content[:160])
    return result


async def recall(
    *,
    agent: str,
    query: str,
    project_id: str,
    memory_type: MemoryType | None = None,
    user_id: str | None = None,
    limit: int = 8,
) -> list[dict]:
    """Semantic recall, optionally filtered to one taxonomy type."""
    m = _memory()
    filters = {"type": memory_type.value} if memory_type else None
    res = await m.search(
        query=query,
        user_id=_scope(project_id) if user_id is None else f"user:{user_id}",
        agent_id=None,
        limit=limit,
        filters=filters,
    )
    hits = res.get("results", res) if isinstance(res, dict) else res
    await _emit(
        "RECALL", agent, memory_type.value if memory_type else "any",
        _scope(project_id), f"q={query[:120]} → {len(hits)} hit(s)",
    )
    return hits


async def build_context_block(*, agent: str, project_id: str, query: str, limit: int = 6) -> str:
    """Recall across all types and render an injectable prompt block."""
    hits = await recall(agent=agent, query=query, project_id=project_id, limit=limit)
    if not hits:
        return ""
    lines = [
        f"- [{h.get('metadata', {}).get('type', 'memory')}] {h.get('memory', h)}"
        for h in hits
    ]
    return "Relevant memory from previous runs:\n" + "\n".join(lines)
