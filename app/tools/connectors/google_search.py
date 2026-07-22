"""Google Web Search with a native custom DATE RANGE.

Google's "Tools → Any time → Custom range" is just a URL parameter —
`tbs=cdr:1,cd_min:M/D/YYYY,cd_max:M/D/YYYY` — so we navigate straight to the date-filtered
results (no UI clicking). A real browser (Playwright/Chromium, already in the image) does the
fetch because Google gates plain requests behind consent/JS; Scrapling's browser-impersonated
fetch is the fallback if the browser step fails. Best-effort: Google actively throttles
automated search, so the fleet does not depend on it — the ingestion date cut is the guarantee.
"""

import asyncio
import contextlib
import re
from datetime import date, timedelta
from urllib.parse import parse_qs, quote_plus, urlparse

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

_SKIP_HOSTS = ("google.", "gstatic.", "googleapis.", "googleusercontent", "ggpht.",
               "gvt1.", "gvt2.", "youtube.com", "schema.org", "w3.org")
# a result link wrapping an <h3> title — Google's stable-ish SERP shape
_RESULT_RE = re.compile(r'<a href="(/url\?[^"]+|https?://[^"]+)"[^>]*>(?:(?!</a>).)*?<h3[^>]*>(.*?)</h3>',
                        re.I | re.S)
_TAG_RE = re.compile(r"<[^>]+>")
# Browser-MCP snapshot shapes: markdown [Title](url) and a11y  link "Title" … url
_MD_LINK_RE = re.compile(r'\[([^\]\n]{3,200})\]\((https?://[^)\s]+)\)')
_A11Y_LINK_RE = re.compile(r'"([^"\n]{3,200})"[^\n]{0,80}?((?:https?://|/url\?)\S+)')


def _cdr_url(query: str, days_back: int) -> str:
    """Google search URL with the native custom date range (M/D/YYYY, as the UI produces)."""
    end = date.today()
    start = end - timedelta(days=max(1, days_back))
    tbs = (f"cdr:1,cd_min:{start.month}/{start.day}/{start.year},"
           f"cd_max:{end.month}/{end.day}/{end.year}")
    return (f"https://www.google.com/search?q={quote_plus(query)}&hl=en&num=20"
            f"&tbs={quote_plus(tbs)}")


def _real_url(href: str) -> str:
    if href.startswith("/url?"):
        q = parse_qs(urlparse(href).query).get("q", [""])[0]
        return q
    return href


def _parse_serp(html: str, query: str, group: str = "") -> list[RawArticle]:
    """Parse result (title, url) pairs from raw SERP HTML (Playwright/Scrapling) OR from a
    Browser-MCP accessibility/markdown snapshot."""
    html = html or ""
    pairs = [(m.group(1), m.group(2)) for m in _RESULT_RE.finditer(html)]
    if not pairs:   # snapshot fallback: markdown links [Title](url) or a11y link "Title" url
        pairs = ([(u, t) for t, u in _MD_LINK_RE.findall(html)]
                 + [(u, t) for t, u in _A11Y_LINK_RE.findall(html)])
    out: list[RawArticle] = []
    seen: set[str] = set()
    for href, title in pairs:
        url = _real_url(str(href).strip())
        host = urlparse(url).netloc.removeprefix("www.")
        title = re.sub(r"\s+", " ", _TAG_RE.sub("", str(title))).strip()
        # a real headline has spaces and length; reject asset refs / MIME types / bare tokens
        if (not url.startswith("http") or url in seen or any(s in host for s in _SKIP_HOSTS)
                or len(title) < 12 or " " not in title
                or re.fullmatch(r"[\w/.+-]+", title)):
            continue
        seen.add(url)
        out.append(RawArticle(
            publisher_name=host, title=title, content="", publisher_domain=host, url=url,
            language="en", source="google_search", query_group=group, original_query=query))
    return out


async def _browser_html(url: str) -> str:
    """Fetch a Google SERP through real Chromium, dismissing the consent wall if present."""
    with contextlib.suppress(Exception):
        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox", "--disable-dev-shm-usage"])
            try:
                page = await browser.new_page(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                # consent interstitial (EU/first-visit): accept if a button is there
                for label in ("Accept all", "I agree", "Accept"):
                    btn = page.get_by_role("button", name=re.compile(label, re.I))
                    with contextlib.suppress(Exception):
                        if await btn.count():
                            await btn.first.click(timeout=3000)
                            await page.wait_for_timeout(1200)
                            break
                await page.wait_for_timeout(1500)
                return await page.content()
            finally:
                with contextlib.suppress(Exception):
                    await browser.close()
    return ""


async def _collect(url: str, query: str, limit: int) -> list[RawArticle]:
    """Layered fetch+parse of a date-filtered Google SERP — return the FIRST layer that
    actually yields results, so an unparseable/blocked response falls through:
    1. Browser MCP agent (if enabled) — works through consent / cookie selection dialogs.
    2. Playwright / real Chromium — dismisses the consent wall via a button click.
    3. Scrapling browser-impersonated fetch.
    NOTE: none reliably solves a hard CAPTCHA; consent/selection dialogs are handled, an
    actual challenge yields nothing (best-effort source)."""
    from app.tools.scraping import browser_mcp

    if browser_mcp.enabled():
        with contextlib.suppress(Exception):
            arts = _parse_serp(await browser_mcp.navigate_and_read(url), query)
            if arts:
                return arts[:limit]
    arts = _parse_serp(await _browser_html(url), query)   # real Chromium
    if arts:
        return arts[:limit]
    with contextlib.suppress(Exception):                  # Scrapling impersonated fetch
        from app.tools.scraping.fetcher import fetch_html

        arts = _parse_serp(await fetch_html(url, timeout=20, impersonate=True), query)
    return arts[:limit]


class GoogleSearchConnector(Connector):
    name = "google_search"
    capabilities = Capabilities(date_range=True, language=True, max_results=True)

    def enabled(self) -> bool:
        # opt-out; best-effort source (Google throttles automated search)
        return get_settings().google_search_enabled

    async def search(self, queries: list[str], filters: SearchFilters) -> ConnectorResult:
        result = ConnectorResult()
        days = filters.days_back or 2
        sem = asyncio.Semaphore(2)   # cap concurrent Chromium instances (memory)

        async def one(query: str) -> None:
            async with sem:
                arts = await _collect(_cdr_url(query, days), query, filters.max_results)
            if not arts:
                result.errors.append(f"{self.name}:{query}: no results (blocked/empty)")
            result.articles.extend(arts)

        await asyncio.gather(*(one(q) for q in queries))
        log.info("connector.google_search", articles=len(result.articles),
                 errors=len(result.errors))
        return result
