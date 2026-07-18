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
from app.llm_gateway.registry import ModelSpec, fallback_spec, primary_spec, specs
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
def _build_for(primary_provider: str) -> Model:
    primary = _build_single(specs()[primary_provider])
    other = "claude" if primary_provider == "gpt" else "gpt"
    secondary = _build_single(specs()[other])

    if primary and secondary:
        return FallbackModel(primary, secondary)
    if primary:
        log.warning("llm_gateway.no_fallback", detail="secondary provider keys missing")
        return primary
    if secondary:
        log.warning("llm_gateway.primary_missing", detail="using secondary provider only")
        return secondary
    raise RuntimeError(
        "No LLM provider configured — set ANTHROPIC_API_KEY or AZURE_OPENAI_API_KEY/ENDPOINT"
    )


def build_model(stage: str | None = None) -> Model:
    from app.llm_gateway.registry import provider_for

    return _build_for(provider_for(stage))


def active_spec(stage: str | None = None) -> ModelSpec:
    """Spec of the provider that will actually serve as primary given current keys."""
    s = get_settings()
    p = primary_spec(stage)
    if p.provider == "gpt" and not (s.azure_openai_api_key and s.azure_openai_endpoint):
        return fallback_spec(stage)
    if p.provider == "claude" and not s.anthropic_api_key:
        return fallback_spec(stage)
    return p
