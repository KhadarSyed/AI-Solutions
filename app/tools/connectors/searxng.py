"""SearXNG — self-hosted metasearch (news category), free, aggregates engines."""

import asyncio
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


class SearxngConnector(Connector):
    name = "searxng"
    capabilities = Capabilities(language=True, max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().searxng_url)

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        base = get_settings().searxng_url.rstrip("/")

        async def one(query: str) -> None:
            params = {
                "q": query,
                "format": "json",
                "categories": "news",
                "language": filters.language or "en",
            }
            try:
                async with httpx.AsyncClient(timeout=20) as client:
                    resp = await client.get(f"{base}/search", params=params)
                    resp.raise_for_status()
                    payload = resp.json()
                for item in payload.get("results", [])[: filters.max_results]:
                    url = item.get("url", "")
                    result.articles.append(
                        RawArticle(
                            publisher_name=item.get("engine", ""),
                            title=item.get("title", ""),
                            content=item.get("content", "") or "",
                            publisher_domain=urlparse(url).netloc.removeprefix("www."),
                            url=url,
                            language=filters.language or "",
                            source=self.name,
                            original_query=query,
                        )
                    )
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        log.info("connector.searxng", articles=len(result.articles), errors=len(result.errors))
        return result
