"""Page fetching — httpx first; Scrapling's adaptive fetch as the fallback for
sources that block plain clients (the self-heal ladder can escalate to it too)."""

import asyncio

import httpx

from app.observability.logging import get_logger

log = get_logger(__name__)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)


async def fetch_html(url: str, timeout: float = 15.0, impersonate: bool = False) -> str:
    if not impersonate:
        try:
            async with httpx.AsyncClient(
                follow_redirects=True, timeout=timeout, headers={"User-Agent": _UA}
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                return resp.text
        except Exception as exc:
            log.info("fetch.httpx_failed_trying_scrapling", url=url, error=str(exc)[:120])

    # Scrapling: browser-impersonated fetch, sync API → thread
    def _scrapling_fetch() -> str:
        from scrapling.fetchers import StealthyFetcher

        page = StealthyFetcher.fetch(url, headless=True, network_idle=True)
        return page.html_content or ""

    return await asyncio.to_thread(_scrapling_fetch)
