from app.llm_gateway import cache
from app.llm_gateway.registry import clamp_output_tokens, cost_usd, specs


def test_gpt4o_clamped_to_16384():
    spec = specs()["gpt"]
    assert clamp_output_tokens(spec, 32000) == 16384


def test_claude_allows_32k():
    spec = specs()["claude"]
    assert clamp_output_tokens(spec, 32000) == 32000


def test_cost_math():
    spec = specs()["gpt"]  # $2.50 in / $10.00 out per 1M
    assert cost_usd(spec, 1_000_000, 0) == 2.50
    assert cost_usd(spec, 0, 1_000_000) == 10.00
    assert round(cost_usd(spec, 1000, 500), 6) == round(0.0025 + 0.005, 6)


def test_cache_key_deterministic_and_sensitive():
    k1 = cache.cache_key("gpt-4o", "prompt", "sys", "Out")
    k2 = cache.cache_key("gpt-4o", "prompt", "sys", "Out")
    k3 = cache.cache_key("gpt-4o", "prompt!", "sys", "Out")
    assert k1 == k2 != k3
    assert k1.startswith("llm:cache:")
