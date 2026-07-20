"""Keyed connectors — enabled only when their env keys are set.

SerpAPI and Tavily are implemented; Apify and XPOz are registered stubs behind
the same ABC (Apify actor choice and the XPOz service definition are pending)."""

import asyncio
import contextlib
from datetime import datetime
from urllib.parse import urlparse

import httpx

from app.config.settings import get_settings
from app.observability.logging import get_logger
from app.tools.connectors.base import (
    Capabilities,
    Connector,
    ConnectorResult,
    RawArticle,
    SearchFilters,
)

log = get_logger(__name__)


class SerpApiConnector(Connector):
    name = "serpapi"
    capabilities = Capabilities(date_range=True, language=True, country=True, max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().serpapi_api_key)

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        key = get_settings().serpapi_api_key

        async def one(query: str) -> None:
            params = {
                "engine": "google_news",
                "q": query,
                "api_key": key,
                "num": filters.max_results,
            }
            if filters.language:
                params["hl"] = filters.language
            if filters.country and filters.country != "all":
                params["gl"] = filters.country
            try:
                async with httpx.AsyncClient(timeout=25) as client:
                    resp = await client.get("https://serpapi.com/search.json", params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                for item in payload.get("news_results", [])[: filters.max_results]:
                    url = item.get("link", "")
                    published = None
                    if item.get("date"):
                        with contextlib.suppress(ValueError):
                            published = datetime.strptime(
                                item["date"].split(",")[0], "%m/%d/%Y"
                            ).date()
                    result.articles.append(
                        RawArticle(
                            publisher_name=(item.get("source") or {}).get("name", "")
                            if isinstance(item.get("source"), dict) else str(item.get("source", "")),
                            title=item.get("title", ""),
                            content=item.get("snippet", "") or "",
                            publisher_domain=urlparse(url).netloc.removeprefix("www."),
                            published_date=published,
                            url=url,
                            language=filters.language or "",
                            source=self.name,
                            original_query=query,
                        )
                    )
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        return result


class TavilyConnector(Connector):
    name = "tavily"
    capabilities = Capabilities(date_range=True, max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().tavily_api_key)

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        from tavily import AsyncTavilyClient

        client = AsyncTavilyClient(api_key=get_settings().tavily_api_key)

        async def one(query: str) -> None:
            try:
                resp = await client.search(
                    query=query, topic="news", days=filters.days_back,
                    max_results=min(filters.max_results, 20), include_raw_content=True,
                )
                for item in resp.get("results", []):
                    url = item.get("url", "")
                    result.articles.append(
                        RawArticle(
                            title=item.get("title", ""),
                            content=item.get("raw_content") or item.get("content", "") or "",
                            publisher_domain=urlparse(url).netloc.removeprefix("www."),
                            url=url,
                            source=self.name,
                            original_query=query,
                        )
                    )
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        return result


class ApifyConnector(Connector):
    """Runs two Apify actors (Google Search Results Scraper + RAG Web Browser)
    and normalizes their dataset items to RawArticle."""

    name = "apify"
    capabilities = Capabilities(max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().apify_token)

    async def _run_actor(self, actor: str, query: str, filters: SearchFilters) -> list[dict]:
        s = get_settings()
        url = (f"https://api.apify.com/v2/acts/{actor.replace('/', '~')}"
               "/run-sync-get-dataset-items")
        body = {"queries": query, "query": query,
                "maxResults": filters.max_results,
                "maxRequestsPerCrawl": filters.max_results}
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(url, params={"token": s.apify_token}, json=body)
            resp.raise_for_status()
            data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        s = get_settings()

        async def one(actor: str, query: str) -> None:
            try:
                for item in await self._run_actor(actor, query, filters):
                    url = item.get("url") or item.get("loadedUrl") or item.get("link", "")
                    if not url:
                        continue
                    result.articles.append(RawArticle(
                        publisher_name=item.get("source") or item.get("displayedUrl", "") or "",
                        title=item.get("title", "") or "",
                        content=(item.get("description") or item.get("text")
                                 or item.get("snippet", "") or ""),
                        publisher_domain=urlparse(url).netloc.removeprefix("www."),
                        url=url, source=self.name, medium="news", original_query=query,
                    ))
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        tasks = [one(a, q) for q in queries
                 for a in (s.apify_actor_search, s.apify_actor_rag)]
        await asyncio.gather(*tasks)
        return result
