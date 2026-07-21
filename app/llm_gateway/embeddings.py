"""Embeddings — always Azure text-embedding-3-small (1536-dim), regardless of
LLM_PROVIDER: Anthropic has no embeddings API. Deliberate, documented asymmetry."""

import time
from functools import lru_cache

from openai import AsyncAzureOpenAI

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

EMBEDDING_DIM = 1536

# Cached availability probe. Retrieval uses this to decide vector vs. lexical recall so we
# never pay a failing Azure round-trip per query, and so the system auto-heals to the
# vector path the moment the deployment appears.
_AVAIL_TTL = 300.0  # seconds
_avail_state: dict = {"ok": None, "checked_at": 0.0}


@lru_cache
def _client() -> AsyncAzureOpenAI:
    s = get_settings()
    if not (s.azure_openai_api_key and s.azure_openai_endpoint):
        raise RuntimeError("Embeddings require AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT")
    return AsyncAzureOpenAI(
        api_key=s.azure_openai_api_key,
        azure_endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
    )


async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    s = get_settings()
    resp = await _client().embeddings.create(
        model=s.azure_openai_embed_deployment, input=texts
    )
    return [item.embedding for item in resp.data]


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]


async def embeddings_available() -> bool:
    """True if the Azure embedding deployment answers. Cached (TTL) and sticky-on-failure
    so retrieval can cheaply choose the vector path vs. the lexical fallback. Any error
    (missing keys, 404 deployment, network) counts as unavailable."""
    now = time.monotonic()
    if _avail_state["ok"] is not None and (now - _avail_state["checked_at"]) < _AVAIL_TTL:
        return _avail_state["ok"]
    ok = True
    try:
        await embed_one("ping")
    except Exception as exc:
        ok = False
        log.info("embeddings.unavailable", error=str(exc)[:160])
    _avail_state.update(ok=ok, checked_at=now)
    return ok


def reset_embeddings_probe() -> None:
    """Test/ops hook — force the next availability check to re-probe."""
    _avail_state.update(ok=None, checked_at=0.0)
