"""Google News RSS — keyword search feed, free, no key. Captures everything the
feed returns; relevancy filtering happens downstream in the ingestion service."""

import asyncio
from datetime import date, datetime
from urllib.parse import quote_plus, urlparse

import feedparser
import httpx

from app.observability.logging import get_logger
from app.tools.connectors.base import (
    Capabilities,
    Connector,
    ConnectorResult,
    RawArticle,
    SearchFilters,
)

log = get_logger(__name__)

_FEED = "https://news.google.com/rss/search?q={q}&hl={hl}&gl={gl}&ceid={ceid}"


def _edition(country: str | None, language: str | None) -> tuple[str, str, str]:
    """Map a run's country/language to the Google News RSS edition triple (hl, gl, ceid) so
    a country-scoped run hits that country's edition server-side, not the hardcoded US one.
    country=all/None → worldwide English (US edition, Google's global-English default)."""
    lang = (language or "en").split("-")[0].lower()
    if country and country.lower() not in ("all", ""):
        gl = country.upper()[:2]
        return f"{lang}-{gl}", gl, f"{gl}:{lang}"
    return f"{lang}-US", "US", f"US:{lang}"


def _entry_to_article(entry, query: str, group: str) -> RawArticle:
    from app.tools.scraping import gnews

    publisher = ""
    if getattr(entry, "source", None) is not None:
        publisher = getattr(entry.source, "title", "") or ""
    link = getattr(entry, "link", "") or ""
    domain = urlparse(link).netloc.removeprefix("www.")
    decoded = gnews.decode(link)          # offline decode; redirect fallback in orchestrator
    if decoded:
        link, domain = decoded
    published: date | None = None
    published_at: datetime | None = None
    if getattr(entry, "published_parsed", None):
        published_at = datetime(*entry.published_parsed[:6])
        published = published_at.date()
    title = getattr(entry, "title", "") or ""
    # google appends " - Publisher" to titles
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(f" - {publisher}")]
    return RawArticle(
        publisher_name=publisher,
        title=title,
        content=getattr(entry, "summary", "") or "",
        publisher_domain=domain,
        published_date=published,
        published_at=published_at,
        url=link,
        language="en",
        source="google_news_rss",
        query_group=group,
        original_query=query,
    )


def parse_feed(xml_text: str, query: str, group: str, max_results: int) -> list[RawArticle]:
    feed = feedparser.parse(xml_text)
    return [_entry_to_article(e, query, group) for e in feed.entries[:max_results]]


class GoogleNewsRSSConnector(Connector):
    name = "google_news_rss"
    capabilities = Capabilities(date_range=True, language=True, country=True, max_results=True)

    def enabled(self) -> bool:
        return True  # free — always on

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()

        hl, gl, ceid = _edition(filters.country, filters.language)

        async def one(query: str) -> None:
            q = quote_plus(f"{query} when:{filters.days_back}d")
            url = _FEED.format(q=q, hl=hl, gl=gl, ceid=ceid)
            try:
                async with httpx.AsyncClient(timeout=20, follow_redirects=True) as client:
                    resp = await client.get(url)
                    resp.raise_for_status()
                result.articles.extend(
                    parse_feed(resp.text, query, group="", max_results=filters.max_results)
                )
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        log.info("connector.google_news_rss", queries=len(queries),
                 articles=len(result.articles), errors=len(result.errors))
        return result
