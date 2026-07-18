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

_FEED = "https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"


def _entry_to_article(entry, query: str, group: str) -> RawArticle:
    publisher = ""
    if getattr(entry, "source", None) is not None:
        publisher = getattr(entry.source, "title", "") or ""
    link = getattr(entry, "link", "") or ""
    published: date | None = None
    if getattr(entry, "published_parsed", None):
        published = datetime(*entry.published_parsed[:6]).date()
    title = getattr(entry, "title", "") or ""
    # google appends " - Publisher" to titles
    if publisher and title.endswith(f" - {publisher}"):
        title = title[: -len(f" - {publisher}")]
    return RawArticle(
        publisher_name=publisher,
        title=title,
        content=getattr(entry, "summary", "") or "",
        publisher_domain=urlparse(link).netloc.removeprefix("www."),
        published_date=published,
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
    capabilities = Capabilities(date_range=True, language=True, max_results=True)

    def enabled(self) -> bool:
        return True  # free — always on

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()

        async def one(query: str) -> None:
            q = quote_plus(f"{query} when:{filters.days_back}d")
            url = _FEED.format(q=q)
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
