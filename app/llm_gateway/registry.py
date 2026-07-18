"""Model registry — ids, prices (USD per 1M tokens), and per-model output clamps.

The clamp exists because MAX_OUTPUT_TOKENS (32k) exceeds what some models accept:
gpt-4o rejects >16384 output tokens with a 400.
"""

from dataclasses import dataclass

from app.config.settings import get_settings


@dataclass(frozen=True)
class ModelSpec:
    provider: str            # "gpt" | "claude"
    model_id: str            # provider-native id / deployment name
    input_price: float       # USD per 1M input tokens
    output_price: float      # USD per 1M output tokens
    max_output_clamp: int


def specs() -> dict[str, ModelSpec]:
    s = get_settings()
    return {
        "gpt": ModelSpec(
            provider="gpt",
            model_id=s.azure_openai_chat_deployment,
            input_price=2.50,
            output_price=10.00,
            max_output_clamp=16384,
        ),
        "claude": ModelSpec(
            provider="claude",
            model_id="claude-sonnet-4-5",
            input_price=3.00,
            output_price=15.00,
            max_output_clamp=32000,
        ),
    }


def provider_for(stage: str | None = None) -> str:
    """Stage-scoped provider: per-stage override wins, else the global default."""
    s = get_settings()
    if stage and stage in s.llm_provider_overrides:
        override = s.llm_provider_overrides[stage]
        if override in ("gpt", "claude"):
            return override
    return s.llm_provider


def primary_spec(stage: str | None = None) -> ModelSpec:
    return specs()[provider_for(stage)]


def fallback_spec(stage: str | None = None) -> ModelSpec:
    other = "claude" if provider_for(stage) == "gpt" else "gpt"
    return specs()[other]


def clamp_output_tokens(spec: ModelSpec, requested: int | None = None) -> int:
    req = requested or get_settings().max_output_tokens
    return min(req, spec.max_output_clamp)


def cost_usd(spec: ModelSpec, input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * spec.input_price + output_tokens * spec.output_price) / 1_000_000
