"""XPOz social-listening connector (Reddit + TikTok) over its MCP server.

Static Bearer auth (no OAuth). Normalizes posts to RawArticle with medium="social"
so social coverage enriches the corpus without skewing news-only charts."""
import asyncio
import contextlib
import json
from datetime import timedelta

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


def _reddit_to_article(post: dict, subject: str) -> RawArticle:
    permalink = post.get("permalink", "") or ""
    url = permalink if permalink.startswith("http") else f"https://reddit.com{permalink}"
    return RawArticle(
        publisher_name=f"Reddit — r/{post.get('subreddit', '')}".rstrip(" —"),
        title=post.get("title", "") or (post.get("selftext", "") or "")[:120],
        content=post.get("selftext", "") or "",
        publisher_domain="reddit.com", url=url, author=post.get("author", "") or "",
        source="reddit", medium="social", subject_brand=subject, original_query=subject,
    )


def _tiktok_to_article(post: dict, subject: str) -> RawArticle:
    author = post.get("author") or {}
    handle = author.get("uniqueId") or author.get("nickname") or ""
    url = post.get("webVideoUrl") or post.get("url", "") or ""
    return RawArticle(
        publisher_name="TikTok", title=(post.get("desc", "") or "")[:120],
        content=post.get("desc", "") or "", publisher_domain="tiktok.com",
        url=url or f"https://tiktok.com/video/{post.get('id', '')}",
        author=f"@{handle}" if handle else "", source="tiktok", medium="social",
        subject_brand=subject, original_query=subject,
    )


class XPozConnector(Connector):
    name = "xpoz"
    capabilities = Capabilities(date_range=True, max_results=True)

    def enabled(self) -> bool:
        return bool(get_settings().xpoz_api_key)

    async def _call(self, tool: str, args: dict) -> list[dict]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        s = get_settings()
        async with streamablehttp_client(
                s.xpoz_mcp_url, headers={"Authorization": f"Bearer {s.xpoz_api_key}"},
                timeout=timedelta(seconds=40), sse_read_timeout=timedelta(seconds=40)
        ) as (r, w, *_), ClientSession(r, w) as session:
            await session.initialize()
            res = await session.call_tool(tool, args,
                                          read_timeout_seconds=timedelta(seconds=40))
        text = "".join(getattr(i, "text", "") or "" for i in res.content)
        with contextlib.suppress(Exception):
            data = json.loads(text)
            if isinstance(data, dict):
                return data.get("items") or data.get("results") or data.get("data") or []
            if isinstance(data, list):
                return data
        return []

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        limit = min(filters.max_results, 25)

        async def one(query: str) -> None:
            try:
                for p in await self._call("getRedditPostsByKeywords",
                                          {"query": query, "limit": limit,
                                           "responseType": "full"}):
                    result.articles.append(_reddit_to_article(p, query))
            except Exception as exc:
                result.errors.append(f"{self.name}:reddit:{query}: {exc}")
            try:
                for p in await self._call("getTiktokPostsByKeywords",
                                          {"query": query, "limit": limit,
                                           "responseType": "full"}):
                    result.articles.append(_tiktok_to_article(p, query))
            except Exception as exc:
                result.errors.append(f"{self.name}:tiktok:{query}: {exc}")

        await asyncio.gather(*(one(q) for q in queries))
        log.info("connector.xpoz", queries=len(queries),
                 articles=len(result.articles), errors=len(result.errors))
        return result
