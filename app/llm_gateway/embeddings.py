"""Embeddings — pluggable backend.

- "local" (default): fastembed ONNX (BAAI/bge-small-en-v1.5, 384-dim), fully offline,
  no external API. This is what makes semantic recall work without any Azure dependency.
- "azure": Azure OpenAI text-embedding-3-small (1536-dim). Anthropic has no embeddings API,
  so the LLM provider is irrelevant here.

The article_embeddings column dimension must match the active backend (see settings)."""

import time
from functools import lru_cache

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


def embedding_dim() -> int:
    s = get_settings()
    return s.local_embedding_dim if s.embedding_backend == "local" else 1536


# Back-compat constant (azure default); prefer embedding_dim() for the active backend.
EMBEDDING_DIM = 1536

# Cached availability probe. Retrieval uses this to decide vector vs. lexical recall.
_AVAIL_TTL = 300.0
_avail_state: dict = {"ok": None, "checked_at": 0.0}


# --- local (fastembed) ---------------------------------------------------------------
@lru_cache
def _local_model():
    from fastembed import TextEmbedding

    return TextEmbedding(model_name=get_settings().local_embedding_model)


def _local_embed_sync(texts: list[str]) -> list[list[float]]:
    return [[float(x) for x in vec] for vec in _local_model().embed(list(texts))]


# --- azure ---------------------------------------------------------------------------
@lru_cache
def _azure_client():
    from openai import AsyncAzureOpenAI

    s = get_settings()
    if not (s.azure_openai_api_key and s.azure_openai_endpoint):
        raise RuntimeError("Azure embeddings require AZURE_OPENAI_API_KEY and _ENDPOINT")
    return AsyncAzureOpenAI(
        api_key=s.azure_openai_api_key,
        azure_endpoint=s.azure_openai_endpoint,
        api_version=s.azure_openai_api_version,
    )


async def _azure_embed(texts: list[str]) -> list[list[float]]:
    s = get_settings()
    resp = await _azure_client().embeddings.create(
        model=s.azure_openai_embed_deployment, input=texts
    )
    return [item.embedding for item in resp.data]


# --- public API ----------------------------------------------------------------------
async def embed(texts: list[str]) -> list[list[float]]:
    if not texts:
        return []
    if get_settings().embedding_backend == "local":
        import asyncio

        return await asyncio.to_thread(_local_embed_sync, texts)
    return await _azure_embed(texts)


async def embed_one(text: str) -> list[float]:
    return (await embed([text]))[0]


async def embeddings_available() -> bool:
    """True if the active embedding backend answers. Cached (TTL), sticky-on-failure, so
    retrieval can cheaply choose the vector path vs. the lexical fallback. For the local
    backend this is effectively always True once the model is cached."""
    now = time.monotonic()
    if _avail_state["ok"] is not None and (now - _avail_state["checked_at"]) < _AVAIL_TTL:
        return _avail_state["ok"]
    ok = True
    try:
        await embed_one("ping")
    except Exception as exc:
        ok = False
        log.info("embeddings.unavailable", backend=get_settings().embedding_backend,
                 error=str(exc)[:160])
    _avail_state.update(ok=ok, checked_at=now)
    return ok


def reset_embeddings_probe() -> None:
    """Test/ops hook — force the next availability check to re-probe."""
    _avail_state.update(ok=None, checked_at=0.0)
