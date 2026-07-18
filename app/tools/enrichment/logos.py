"""LogoResolver — brand/competitor logos, base64-embedded for self-contained HTML.

Ladder: research memory (learned domain) → DDG official-site lookup → favicon
service → base64 data URI. Anything unresolved gets a monogram badge spec."""

import asyncio
import base64
import contextlib
import re
from urllib.parse import urlparse

import httpx

from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)

MONOGRAM_COLORS = ["#2563eb", "#7c3aed", "#0d9488", "#db2777", "#d97706", "#4f46e5"]


def monogram(name: str, index: int = 0) -> dict:
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    return {"name": name, "kind": "monogram", "initials": initials,
            "color": MONOGRAM_COLORS[index % len(MONOGRAM_COLORS)]}


async def _find_domain(name: str) -> str | None:
    def _search() -> str | None:
        from ddgs import DDGS

        with DDGS() as ddgs:
            for r in ddgs.text(f"{name} official website", max_results=3):
                href = r.get("href") or r.get("url") or ""
                host = urlparse(href).netloc.removeprefix("www.")
                if host and not any(b in host for b in
                                    ("wikipedia", "linkedin", "facebook", "youtube")):
                    return host
        return None

    try:
        return await asyncio.to_thread(_search)
    except Exception:
        return None


async def _fetch_logo(domain: str) -> str | None:
    url = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(url)
            resp.raise_for_status()
            if len(resp.content) < 200:      # generic globe placeholder
                return None
            return "data:image/png;base64," + base64.b64encode(resp.content).decode()
    except Exception:
        return None


async def resolve_logos(project_id: str, names: list[str], budget: int = 6) -> list[dict]:
    out: list[dict] = []
    for i, name in enumerate(names[:budget]):
        domain: str | None = None
        with contextlib.suppress(Exception):
            hits = await recall(agent="dashboard", query=f"logo domain for {name}",
                                project_id=project_id, memory_type=MemoryType.RESEARCH, limit=2)
            for h in hits:
                m = re.search(rf"{re.escape(name)}\s*→\s*logo domain\s+(\S+)",
                              str(h.get("memory", "")))
                if m:
                    domain = m.group(1)
        if domain is None:
            domain = await _find_domain(name)

        data_uri = await _fetch_logo(domain) if domain else None
        if data_uri:
            out.append({"name": name, "kind": "image", "data_uri": data_uri,
                        "domain": domain})
            with contextlib.suppress(Exception):
                await remember(agent="dashboard", memory_type=MemoryType.RESEARCH,
                               project_id=project_id,
                               content=f"{name} → logo domain {domain}")
        else:
            out.append(monogram(name, i))
    # anything past budget: monogram
    out += [monogram(n, i) for i, n in enumerate(names[budget:], start=budget)]
    return out
