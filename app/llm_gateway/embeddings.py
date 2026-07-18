"""Embeddings — always Azure text-embedding-3-small (1536-dim), regardless of
LLM_PROVIDER: Anthropic has no embeddings API. Deliberate, documented asymmetry."""

from functools import lru_cache

from openai import AsyncAzureOpenAI

from app.config.settings import get_settings

EMBEDDING_DIM = 1536


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
