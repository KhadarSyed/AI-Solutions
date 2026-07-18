"""DuckDuckGo news — free, no key (ddgs)."""

import asyncio
import contextlib
from datetime import datetime
from urllib.parse import urlparse

from app.observability.logging import get_logger
from app.tools.connectors.base import (
    Capabilities,
    Connector,
    ConnectorResult,
    RawArticle,
    SearchFilters,
)

log = get_logger(__name__)


class DuckDuckGoConnector(Connector):
    name = "duckduckgo"
    capabilities = Capabilities(date_range=True, max_results=True)

    def enabled(self) -> bool:
        return True  # free — always on

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()

        def _one_sync(query: str) -> list[RawArticle]:
            from ddgs import DDGS

            timelimit = "w" if filters.days_back <= 7 else "m"
            articles = []
            with DDGS() as ddgs:
                for item in ddgs.news(query, timelimit=timelimit,
                                      max_results=filters.max_results):
                    url = item.get("url", "")
                    published = None
                    if item.get("date"):
                        with contextlib.suppress(ValueError):
                            published = datetime.fromisoformat(item["date"]).date()
                    articles.append(
                        RawArticle(
                            publisher_name=item.get("source", ""),
                            title=item.get("title", ""),
                            content=item.get("body", "") or "",
                            publisher_domain=urlparse(url).netloc.removeprefix("www."),
                            published_date=published,
                            url=url,
                            source=self.name,
                            original_query=query,
                        )
                    )
            return articles

        for query in queries:  # ddgs is sync + rate-limited; serialize in a thread
            try:
                result.articles.extend(await asyncio.to_thread(_one_sync, query))
            except Exception as exc:
                result.errors.append(f"{self.name}:{query}: {exc}")

        log.info("connector.ddg", articles=len(result.articles), errors=len(result.errors))
        return result
