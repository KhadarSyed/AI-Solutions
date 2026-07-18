"""Reach enrichment — monthly visits per publisher domain.

Order: reference CSV artifact (uploaded once) → bundled data/reach.csv →
SimilarWeb API fallback (key-gated). Reach maps to a 1–5 weight per playbook §10.
"""

import csv
import io
from functools import lru_cache
from pathlib import Path

import httpx

from app.artifacts import keys
from app.artifacts.base import ArtifactNotFound
from app.artifacts.factory import get_artifact_store
from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

_BUNDLED = Path(__file__).resolve().parents[3] / "data" / "reach.csv"

TIER1_DOMAINS = {
    "reuters.com", "bloomberg.com", "wsj.com", "ft.com", "nytimes.com",
    "washingtonpost.com", "apnews.com", "bbc.com", "cnbc.com", "forbes.com",
}


def reach_weight(monthly_reach: int) -> int:
    if monthly_reach <= 100_000:
        return 1
    if monthly_reach <= 500_000:
        return 2
    if monthly_reach <= 800_000:
        return 3
    if monthly_reach <= 1_000_000:
        return 4
    return 5


def _parse_csv(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for row in csv.DictReader(io.StringIO(text)):
        domain = (row.get("domain") or "").strip().lower().removeprefix("www.")
        try:
            out[domain] = int(float(row.get("monthly_reach", 0) or 0))
        except ValueError:
            continue
    return out


@lru_cache
def _bundled_lookup() -> dict[str, int]:
    if _BUNDLED.exists():
        return _parse_csv(_BUNDLED.read_text(encoding="utf-8"))
    return {}


async def _artifact_lookup() -> dict[str, int]:
    try:
        data = await get_artifact_store().get_bytes(keys.REACH_CSV)
        return _parse_csv(data.decode("utf-8"))
    except ArtifactNotFound:
        return {}


async def _similarweb(domain: str) -> int | None:
    key = get_settings().similarweb_api_key
    if not key:
        return None
    url = f"https://api.similarweb.com/v1/website/{domain}/total-traffic-and-engagement/visits"
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(url, params={"api_key": key, "granularity": "monthly",
                                                 "main_domain_only": "true"})
            resp.raise_for_status()
            visits = resp.json().get("visits", [])
            if visits:
                return int(visits[-1].get("visits", 0))
    except Exception as exc:
        log.info("reach.similarweb_failed", domain=domain, error=str(exc)[:100])
    return None


async def enrich_reach(domains: list[str]) -> dict[str, dict]:
    """domain → {monthly_reach, reach_weight, tier1}."""
    lookup = {**_bundled_lookup(), **(await _artifact_lookup())}
    out: dict[str, dict] = {}
    for domain in {d.lower().removeprefix("www.") for d in domains if d}:
        reach = lookup.get(domain)
        if reach is None:
            reach = await _similarweb(domain) or 0
        out[domain] = {
            "monthly_reach": reach,
            "reach_weight": reach_weight(reach) if reach else 1,
            "tier1": domain in TIER1_DOMAINS,
        }
    return out
