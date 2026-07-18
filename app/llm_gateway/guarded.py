"""GuardedAgent — the ONLY way agents execute LLM calls.

Wraps a pydantic-ai Agent with, in order:
  input guards → Redis cache (temp-0 only) → model call (FallbackModel) →
  output guards → cost row + guardrail events.
"""

import hashlib
import json
import time
import uuid
from typing import Any

from pydantic import BaseModel
from pydantic_ai import Agent

from app.guardrails.runner import guard_input, guard_output
from app.llm_gateway import cache as llm_cache
from app.llm_gateway import cost
from app.llm_gateway.models import active_spec, build_model
from app.llm_gateway.registry import clamp_output_tokens
from app.observability.logging import get_logger

log = get_logger(__name__)

class GuardedAgent[T]:
    def __init__(
        self,
        *,
        purpose: str,
        stage: str | None = None,
        system_prompt: str = "",
        output_type: type[T] = str,
        temperature: float = 0.0,
        cacheable: bool = False,
        user_originated_input: bool = False,
        stakeholder_facing_output: bool = False,
        require_citations: bool = False,
        tools: list | None = None,
        max_output_tokens: int | None = None,
    ):
        self.purpose = purpose
        self.stage = stage
        self.temperature = temperature
        self.cacheable = cacheable and temperature == 0.0
        self.user_originated_input = user_originated_input
        self.stakeholder_facing_output = stakeholder_facing_output
        self.require_citations = require_citations
        self._output_type = output_type
        self._system_prompt = system_prompt

        spec = active_spec()
        self._spec = spec
        self._model_settings = {
            "temperature": temperature,
            "max_tokens": clamp_output_tokens(spec, max_output_tokens),
        }
        self.agent: Agent = Agent(
            build_model(),
            output_type=output_type,
            system_prompt=system_prompt,
            tools=tools or [],
        )

    def _cache_key(self, prompt: str) -> str:
        schema = getattr(self._output_type, "__name__", str(self._output_type))
        return llm_cache.cache_key(self._spec.model_id, prompt, self._system_prompt, schema)

    def _serialize(self, output: Any) -> str:
        if isinstance(output, BaseModel):
            return output.model_dump_json()
        return json.dumps(output, ensure_ascii=False)

    def _deserialize(self, raw: str) -> Any:
        if isinstance(self._output_type, type) and issubclass(self._output_type, BaseModel):
            return self._output_type.model_validate_json(raw)
        return json.loads(raw)

    async def run(self, prompt: str, *, request_id: str | None = None, **agent_kwargs) -> T:
        request_id = request_id or uuid.uuid4().hex[:16]
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()

        safe_prompt = await guard_input(
            prompt, request_id=request_id, user_originated=self.user_originated_input
        )

        if self.cacheable:
            cached = await llm_cache.get(self._cache_key(safe_prompt))
            if cached is not None:
                await cost.record_call(
                    spec=self._spec, request_id=request_id, stage=self.stage,
                    purpose=self.purpose, prompt_sha256=prompt_hash,
                    input_tokens=0, output_tokens=0, latency_ms=0, cache_hit=True,
                )
                return self._deserialize(cached)

        started = time.monotonic()
        status, error, fallback_used = "ok", None, False
        try:
            result = await self.agent.run(
                safe_prompt, model_settings=self._model_settings, **agent_kwargs
            )
        except Exception as exc:
            await cost.record_call(
                spec=self._spec, request_id=request_id, stage=self.stage, purpose=self.purpose,
                prompt_sha256=prompt_hash, input_tokens=0, output_tokens=0,
                latency_ms=int((time.monotonic() - started) * 1000),
                status="error", error=str(exc)[:800],
            )
            raise

        latency_ms = int((time.monotonic() - started) * 1000)
        usage = result.usage()
        try:
            used_model = getattr(result, "response", None)
            fallback_used = bool(
                used_model and self._spec.model_id not in str(getattr(used_model, "model_name", ""))
            )
        except Exception:
            fallback_used = False

        output = result.output
        if isinstance(output, str):
            output = await guard_output(
                output,
                request_id=request_id,
                stakeholder_facing=self.stakeholder_facing_output,
                require_citations=self.require_citations,
            )

        await cost.record_call(
            spec=self._spec, request_id=request_id, stage=self.stage, purpose=self.purpose,
            prompt_sha256=prompt_hash,
            input_tokens=usage.input_tokens or 0,
            output_tokens=usage.output_tokens or 0,
            latency_ms=latency_ms, fallback_used=fallback_used, status=status, error=error,
        )

        if self.cacheable:
            await llm_cache.put(self._cache_key(safe_prompt), self._serialize(output))

        log.info(
            "llm_gateway.call", purpose=self.purpose, stage=self.stage,
            latency_ms=latency_ms, in_tok=usage.input_tokens, out_tok=usage.output_tokens,
            fallback=fallback_used, request_id=request_id,
        )
        return output
