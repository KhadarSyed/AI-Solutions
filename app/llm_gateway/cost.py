"""Cost accounting — one llm_calls row per gateway call."""

from app.db.base import get_sessionmaker
from app.db.models import LLMCall
from app.llm_gateway.registry import ModelSpec, cost_usd


async def record_call(
    *,
    spec: ModelSpec,
    request_id: str | None,
    stage: str | None,
    purpose: str | None,
    prompt_sha256: str | None,
    input_tokens: int,
    output_tokens: int,
    latency_ms: int | None,
    cache_hit: bool = False,
    fallback_used: bool = False,
    status: str = "ok",
    error: str | None = None,
) -> None:
    async with get_sessionmaker()() as session, session.begin():
        session.add(
            LLMCall(
                request_id=request_id,
                provider=spec.provider,
                model=spec.model_id,
                stage=stage,
                purpose=purpose,
                prompt_sha256=prompt_sha256,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=cost_usd(spec, input_tokens, output_tokens),
                latency_ms=latency_ms,
                cache_hit=cache_hit,
                fallback_used=fallback_used,
                status=status,
                error=error,
            )
        )
