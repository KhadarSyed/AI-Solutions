"""LogoResolver — brand/competitor logos, base64-embedded for self-contained HTML.

Ladder: research memory (learned logo URL) → DDG official-site lookup → REAL logo
from the homepage (og:image / apple-touch-icon / icon / header <img> logo) → Google
favicon → monogram badge. The real-logo step is what replaces the bare "B" badge."""

import asyncio
import base64
import contextlib
import re
from urllib.parse import urljoin, urlparse

import httpx

from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger

log = get_logger(__name__)

MONOGRAM_COLORS = ["#2563eb", "#7c3aed", "#0d9488", "#db2777", "#d97706", "#4f46e5"]

# logo candidates in the page <head>/<header>, most brand-representative first
_OG_A = re.compile(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)', re.I)
_OG_B = re.compile(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image["\']', re.I)
_APPLE = re.compile(r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]+href=["\']([^"\']+)', re.I)
_ICON = re.compile(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]+href=["\']([^"\']+)', re.I)
_IMG_LOGO = re.compile(r'<img\b[^>]*(?:class|id|alt|src)=["\'][^"\']*logo[^"\']*["\'][^>]*>', re.I)
_IMG_SRC = re.compile(r'\bsrc=["\']([^"\']+)', re.I)


def _extract_logo_url(html: str, base_url: str) -> str | None:
    """Best brand logo URL from homepage HTML, or None."""
    for rx in (_OG_A, _OG_B, _APPLE, _ICON):
        m = rx.search(html or "")
        if m and m.group(1).strip():
            return urljoin(base_url, m.group(1).strip())
    tag = _IMG_LOGO.search(html or "")
    if tag:
        src = _IMG_SRC.search(tag.group(0))
        if src:
            return urljoin(base_url, src.group(1).strip())
    return None


def _valid_image_bytes(data: bytes, content_type: str) -> tuple[bool, str]:
    """(is_image, mime). Sniffs magic bytes when the server mislabels content."""
    if not (100 <= len(data) <= 1_500_000):
        return False, ""
    ctype = (content_type or "").split(";")[0].strip().lower()
    if ctype.startswith("image/"):
        return True, ctype
    head = data[:256]
    if data[:8].startswith(b"\x89PNG"):
        return True, "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return True, "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return True, "image/gif"
    if b"<svg" in head.lower():
        return True, "image/svg+xml"
    if data[:4] == b"RIFF" and b"WEBP" in head:
        return True, "image/webp"
    return False, ""


async def _fetch_image(url: str) -> str | None:
    """Download an image URL → base64 data-URI, validated."""
    try:
        async with httpx.AsyncClient(timeout=12, follow_redirects=True) as client:
            resp = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
        ok, mime = _valid_image_bytes(resp.content, resp.headers.get("content-type", ""))
        if not ok:
            return None
        return f"data:{mime};base64," + base64.b64encode(resp.content).decode()
    except Exception:
        return None


async def _real_logo(domain: str) -> str | None:
    """Fetch the homepage and extract the real brand logo (not a favicon)."""
    with contextlib.suppress(Exception):
        from app.tools.scraping.fetcher import fetch_html

        html = await fetch_html(f"https://{domain}", timeout=12)
        logo_url = _extract_logo_url(html, f"https://{domain}")
        if logo_url:
            return await _fetch_image(logo_url)
    return None


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


# known official domains for the tracked brands (BeOne = the biotech, not other "BeOne"s),
# extendable via the BRAND_DOMAINS env override (JSON {brand: domain})
_DEFAULT_DOMAINS = {
    "beone": "beonemedicines.com", "beone medicines": "beonemedicines.com",
    "trane": "trane.com", "otsuka": "otsuka.com",
    # BeOne's actual oncology/biotech rivals
    "gsk": "gsk.com", "glaxosmithkline": "gsk.com", "sanofi": "sanofi.com",
    "takeda": "takeda.com", "takeda pharmaceutical": "takeda.com", "argenx": "argenx.com",
    "astrazeneca": "astrazeneca.com",
}


def _domain_override(name: str) -> str | None:
    import json

    from app.config.settings import get_settings
    key = (name or "").strip().lower()
    with contextlib.suppress(Exception):
        env = json.loads(get_settings().brand_domains or "{}")
        for k, v in env.items():
            if k.strip().lower() == key:
                return v
    return _DEFAULT_DOMAINS.get(key)


async def _domain_for(project_id: str, name: str) -> str | None:
    """Resolve a brand's domain: explicit override/known-domain → learned research memory
    → DDG official-site lookup."""
    override = _domain_override(name)
    if override:
        return override
    with contextlib.suppress(Exception):
        hits = await recall(agent="dashboard", query=f"logo domain for {name}",
                            project_id=project_id, memory_type=MemoryType.RESEARCH, limit=2)
        for h in hits:
            m = re.search(rf"{re.escape(name)}\s*→\s*logo domain\s+(\S+)",
                          str(h.get("memory", "")))
            if m:
                return m.group(1)
    return await _find_domain(name)


async def logo_urls(project_id: str, names: list[str], budget: int = 6) -> dict[str, str]:
    """Email-safe REMOTE logo URLs per brand (base64 is stripped by mail clients, but a
    remote <img src> loads). Uses the resolved domain + Google's favicon service, which
    reliably returns the brand's own icon. Names without a domain are omitted (caller
    falls back to a monogram chip)."""
    out: dict[str, str] = {}
    for name in names[:budget]:
        domain = await _domain_for(project_id, name)
        if domain:
            out[name] = f"https://www.google.com/s2/favicons?domain={domain}&sz=128"
            with contextlib.suppress(Exception):
                await remember(agent="dashboard", memory_type=MemoryType.RESEARCH,
                               project_id=project_id, content=f"{name} → logo domain {domain}")
    return out


async def resolve_logos(project_id: str, names: list[str], budget: int = 6) -> list[dict]:
    out: list[dict] = []
    for i, name in enumerate(names[:budget]):
        domain: str | None = _domain_override(name)
        if domain is None:
            with contextlib.suppress(Exception):
                hits = await recall(agent="dashboard", query=f"logo domain for {name}",
                                    project_id=project_id, memory_type=MemoryType.RESEARCH,
                                    limit=2)
                for h in hits:
                    m = re.search(rf"{re.escape(name)}\s*→\s*logo domain\s+(\S+)",
                                  str(h.get("memory", "")))
                    if m:
                        domain = m.group(1)
        if domain is None:
            domain = await _find_domain(name)

        # real logo from the homepage first, then the favicon, then a monogram
        data_uri = None
        if domain:
            data_uri = await _real_logo(domain) or await _fetch_logo(domain)
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
