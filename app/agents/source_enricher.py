"""Per-source enrichment sub-agent — resolves missing country/author for
publisher domains: cheap heuristics → research memory → About/Contact page
navigation → LLM extraction → URL cross-verification. Unresolved stays "all".

Learned facts are stored as RESEARCH memories so future runs skip the fetch.
"""

import asyncio
import contextlib
import re

from pydantic import BaseModel, Field

from app.llm_gateway.guarded import GuardedAgent
from app.memory.mem0_service import MemoryType, recall, remember
from app.observability.logging import get_logger
from app.tools.connectors.base import RawArticle
from app.tools.scraping.extractor import extract_byline, extract_metadata
from app.tools.scraping.fetcher import fetch_html

log = get_logger(__name__)

MAX_DOMAIN_FETCHES_PER_RUN = 15

_CCTLD = {
    ".co.uk": "GB", ".uk": "GB", ".de": "DE", ".fr": "FR", ".in": "IN", ".com.au": "AU",
    ".au": "AU", ".ca": "CA", ".jp": "JP", ".cn": "CN", ".sg": "SG", ".ae": "AE",
    ".sa": "SA", ".br": "BR", ".mx": "MX", ".es": "ES", ".it": "IT", ".nl": "NL",
    ".se": "SE", ".ch": "CH", ".ie": "IE", ".nz": "NZ", ".za": "ZA", ".kr": "KR",
}

_ABOUT_LINK = re.compile(r'href="([^"]*(?:about|contact)[^"]*)"', re.I)


class DomainFacts(BaseModel):
    country: str = Field(description='ISO-3166 alpha-2 country code, or "all" if truly unclear')
    author_desk: str = Field(default="", description="Editorial desk or main byline if stated")
    confidence: float = Field(ge=0, le=1)
    evidence: str = Field(default="", description="Short quote or page fact supporting the country")


class _PublisherCountry(BaseModel):
    publisher: str = Field(description="echo the publisher label exactly as given")
    country: str = Field(description='ISO-3166 alpha-2, or "all" if genuinely unknown')


class _PublisherCountries(BaseModel):
    items: list[_PublisherCountry]


async def countries_from_knowledge(publishers: list[str]) -> dict[str, str]:
    """One batched call: infer each publisher's HOME country from the model's own
    knowledge of the outlet — no page fetch. Keyed on the publisher NAME (not the
    domain) because aggregator feeds (news.google.com) share one redirect domain
    across many distinct outlets. Returns {name_lower: country}."""
    if not publishers:
        return {}
    listing = "\n".join(f"- {p}" for p in publishers)
    agent = GuardedAgent(
        purpose="publisher_country", stage="enrich",
        system_prompt=(
            "You are given news publisher NAMES. For each, return its HOME/"
            "headquarters country as an ISO 3166-1 alpha-2 code using your knowledge "
            "of the outlet (e.g. The Economic Times→IN, Reuters→GB, Sports "
            "Illustrated→US, Yahoo→US, The Straits Times→SG). Echo the publisher "
            "label back exactly as given. Return exactly one entry per input; use "
            "\"all\" only when the outlet is genuinely unknown to you."
        ),
        output_type=_PublisherCountries, temperature=0.0, cacheable=True,
    )
    try:
        res = await agent.run("Publishers:\n" + listing)
    except Exception as exc:
        log.warning("enricher.knowledge_failed", error=str(exc)[:150])
        return {}
    return {i.publisher.strip().lower(): i.country for i in res.items if i.country}


def country_from_tld(domain: str) -> str | None:
    d = domain.lower()
    for suffix, code in sorted(_CCTLD.items(), key=lambda kv: -len(kv[0])):
        if d.endswith(suffix):
            return code
    return None


async def _get_page(url: str) -> str:
    """Browser-MCP navigation when enabled, else httpx→Scrapling fetch."""
    from app.tools.scraping import browser_mcp

    if browser_mcp.enabled():
        try:
            return await browser_mcp.navigate_and_read(url)
        except Exception as exc:
            log.info("enricher.browser_mcp_fallback", url=url, error=str(exc)[:120])
    return await fetch_html(url, timeout=12)


async def _facts_from_about_pages(domain: str) -> DomainFacts | None:
    """Navigate homepage → About/Contact links → footer; extract via the gateway."""
    try:
        home = await _get_page(f"https://{domain}")
    except Exception as exc:
        log.info("enricher.home_fetch_failed", domain=domain, error=str(exc)[:120])
        return None

    texts: list[str] = []
    for href in _ABOUT_LINK.findall(home)[:2]:
        url = href if href.startswith("http") else f"https://{domain}/{href.lstrip('/')}"
        try:
            page = await _get_page(url)
            texts.append(page[-6000:])          # footers live at the bottom
        except Exception:
            continue
    texts.append(home[-4000:])                  # homepage footer as last resort

    if not any(texts):
        return None

    extractor = GuardedAgent(
        purpose="domain_facts",
        stage="enrich",
        system_prompt=(
            "You extract publisher facts from About/Contact/footer page fragments. "
            "Return the publisher's home country as ISO alpha-2 (postal addresses, "
            "'based in', legal imprint are strong evidence). If nothing concrete, country='all'."
        ),
        output_type=DomainFacts,
        temperature=0.0,
        cacheable=True,
    )
    try:
        return await extractor.run(
            f"Publisher domain: {domain}\n\nPage fragments:\n" + "\n---\n".join(t for t in texts if t)
        )
    except Exception as exc:
        log.warning("enricher.extract_failed", domain=domain, error=str(exc)[:150])
        return None


async def resolve_domain(domain: str, project_id: str) -> DomainFacts:
    # 1 — research memory (learned on an earlier run)
    try:
        hits = await recall(
            agent="enricher", query=f"publisher facts for {domain}", project_id=project_id,
            memory_type=MemoryType.RESEARCH, limit=2,
        )
        for h in hits:
            text = str(h.get("memory", ""))
            m = re.search(rf"{re.escape(domain)}\s*→\s*country\s+([A-Z]{{2}})", text)
            if m:
                return DomainFacts(country=m.group(1), confidence=0.9, evidence="research memory")
    except Exception:
        pass  # memory outage never blocks enrichment

    # 2 — ccTLD heuristic
    tld_country = country_from_tld(domain)

    # 3 — About/Contact navigation + extraction
    facts = await _facts_from_about_pages(domain)

    if facts and facts.country != "all":
        # 4 — cross-verify against the URL's ccTLD when both exist
        if tld_country and tld_country != facts.country and facts.confidence < 0.8:
            facts.country = tld_country
            facts.evidence += f" (overridden by ccTLD {tld_country})"
        resolved = facts
    elif tld_country:
        resolved = DomainFacts(country=tld_country, confidence=0.7, evidence="ccTLD")
    else:
        resolved = DomainFacts(country="all", confidence=0.0, evidence="unresolved")

    if resolved.country != "all":
        with contextlib.suppress(Exception):  # memory outage never blocks enrichment
            await remember(
                agent="enricher", memory_type=MemoryType.RESEARCH, project_id=project_id,
                content=f"{domain} → country {resolved.country}"
                + (f", desk: {resolved.author_desk}" if resolved.author_desk else "")
                + f" ({resolved.evidence})",
                metadata={"domain": domain},
            )
    return resolved


async def enrich_articles(articles: list[RawArticle], project_id: str) -> dict:
    """Fill missing country/author in place. Fetch budget applies per unique domain."""
    # first, resolve news.google.com redirects to the real outlet (headless browser
    # follows Google's JS) so the real domain shows AND the article page becomes
    # byline-fetchable below. Publisher NAME already comes from the RSS <source>.
    gnews_resolved = 0
    gnews_articles = [a for a in articles
                      if a.publisher_domain == "news.google.com" and a.url]
    if gnews_articles:
        from app.tools.scraping.gnews import resolve_batch_via_browser

        mapping = await resolve_batch_via_browser([a.url for a in gnews_articles])
        for a in gnews_articles:
            hit = mapping.get(a.url)
            if hit:
                a.url, a.publisher_domain = hit
                gnews_resolved += 1

    need = [a for a in articles if a.country in ("", "all") or not a.author]
    domains = list({a.publisher_domain for a in need if a.publisher_domain})
    budget = domains[:MAX_DOMAIN_FETCHES_PER_RUN]

    sem = asyncio.Semaphore(4)
    resolved: dict[str, DomainFacts] = {}

    async def one(domain: str) -> None:
        async with sem:
            resolved[domain] = await resolve_domain(domain, project_id)

    await asyncio.gather(*(one(d) for d in budget))

    stats = {"domains_considered": len(domains), "domains_fetched": len(budget),
             "countries_resolved": 0, "authors_filled": 0, "gnews_resolved": gnews_resolved}
    for a in articles:
        facts = resolved.get(a.publisher_domain)
        if not facts:
            continue
        if a.country in ("", "all") and facts.country != "all":
            a.country = facts.country
            stats["countries_resolved"] += 1
        if not a.author and facts.author_desk:
            a.author = facts.author_desk
            stats["authors_filled"] += 1

    # knowledge-based country fill — the model knows most outlets' home country
    # without a page fetch; keyed on publisher NAME so aggregator redirects
    # (news.google.com) don't collapse many outlets onto one domain
    def _label(a: RawArticle) -> str:
        return (a.publisher_name or a.publisher_domain or "").strip()

    unresolved = {_label(a).lower(): _label(a)
                  for a in articles if a.country in ("", "all") and _label(a)}
    if unresolved:
        known = await countries_from_knowledge(list(unresolved.values()))
        for a in articles:
            if a.country in ("", "all"):
                c = (known.get(_label(a).lower()) or "").strip()
                if c and c.lower() != "all":
                    a.country = c.upper() if len(c) == 2 else c
                    stats["countries_resolved"] += 1

    # article-page byline pass — fetch the real article and extract the byline.
    # skip news.google.com (redirect, not the article) and cap the fetch budget.
    missing_author = [a for a in articles if not a.author and a.url
                      and "news.google.com" not in (a.publisher_domain or "")][:25]
    byline_sem = asyncio.Semaphore(5)

    async def _fill_byline(a: RawArticle) -> None:
        async with byline_sem:
            try:
                html = await fetch_html(a.url, timeout=10)
            except Exception:
                return
        author = extract_byline(html) or extract_metadata(html, a.url).get("author", "")
        if author:
            a.author = author
            stats["authors_filled"] += 1

    await asyncio.gather(*(_fill_byline(a) for a in missing_author))

    log.info("enricher.done", **stats)
    return stats
