"""Redis prompt/response cache — only for deterministic (temperature=0) calls."""

import hashlib
import json

import redis.asyncio as aioredis

from app.config.settings import get_settings

TTL_SECONDS = 24 * 3600
_client: aioredis.Redis | None = None


def _redis() -> aioredis.Redis:
    global _client
    if _client is None:
        _client = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    return _client


def cache_key(model_id: str, prompt: str, system: str | None, output_schema: str | None) -> str:
    payload = json.dumps([model_id, prompt, system or "", output_schema or ""], ensure_ascii=False)
    return "llm:cache:" + hashlib.sha256(payload.encode()).hexdigest()


async def get(key: str) -> str | None:
    try:
        return await _redis().get(key)
    except Exception:
        return None  # cache is best-effort, never fail a call over it


async def put(key: str, value: str) -> None:
    import contextlib

    with contextlib.suppress(Exception):  # cache is best-effort
        await _redis().set(key, value, ex=TTL_SECONDS)
