"""Model factory — the provider switch. Everything LLM-shaped gets its Model here.

LLM_PROVIDER picks the primary; the other provider is wired as FallbackModel
fallback, so cross-provider failover is automatic when keys for both exist.
"""

from functools import lru_cache

from pydantic_ai.models import Model
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.fallback import FallbackModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.azure import AzureProvider

from app.config.settings import get_settings
from app.llm_gateway.registry import ModelSpec, fallback_spec, primary_spec
from app.observability.logging import get_logger

log = get_logger(__name__)


def _build_single(spec: ModelSpec) -> Model | None:
    s = get_settings()
    if spec.provider == "gpt":
        if not (s.azure_openai_api_key and s.azure_openai_endpoint):
            return None
        return OpenAIChatModel(
            spec.model_id,
            provider=AzureProvider(
                azure_endpoint=s.azure_openai_endpoint,
                api_version=s.azure_openai_api_version,
                api_key=s.azure_openai_api_key,
            ),
        )
    if spec.provider == "claude":
        if not s.anthropic_api_key:
            return None
        return AnthropicModel(spec.model_id)
    raise ValueError(f"unknown provider {spec.provider}")


@lru_cache
def build_model() -> Model:
    primary = _build_single(primary_spec())
    secondary = _build_single(fallback_spec())

    if primary and secondary:
        return FallbackModel(primary, secondary)
    if primary:
        log.warning("llm_gateway.no_fallback", detail="secondary provider keys missing")
        return primary
    if secondary:
        log.warning("llm_gateway.primary_missing", detail="using secondary provider only")
        return secondary
    raise RuntimeError(
        "No LLM provider configured — set AZURE_OPENAI_API_KEY/ENDPOINT or ANTHROPIC_API_KEY"
    )


def active_spec() -> ModelSpec:
    """Spec of the provider that will serve as primary given current keys."""
    s = get_settings()
    p = primary_spec()
    if p.provider == "gpt" and not (s.azure_openai_api_key and s.azure_openai_endpoint):
        return fallback_spec()
    if p.provider == "claude" and not s.anthropic_api_key:
        return fallback_spec()
    return p
