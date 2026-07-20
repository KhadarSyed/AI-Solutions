"""Decode Google News redirect URLs to the real article URL + publisher domain.

Google News RSS links look like
  https://news.google.com/rss/articles/CBMi<base64url-payload>...
The payload is a protobuf-ish blob; the article URL appears as a UTF-8 run
inside it. We extract the first http(s) URL we can find; if that fails we fall
back to following the redirect. Best-effort — returns None when we can't resolve.
"""
import base64
import contextlib
import re
from urllib.parse import urlparse

_GNEWS_HOST = "news.google.com"
_URL_RE = re.compile(rb"https?://[^\s\"'<>\\]+")


def is_gnews_url(url: str) -> bool:
    return _GNEWS_HOST in (url or "")


def _domain(url: str) -> str:
    return urlparse(url).netloc.removeprefix("www.")


def decode(url: str) -> tuple[str, str] | None:
    """(real_url, publisher_domain) or None. Offline base64 parse only."""
    if not is_gnews_url(url):
        return None
    m = re.search(r"/articles/([A-Za-z0-9_\-]+)", url)
    if not m:
        return None
    token = m.group(1)
    # strip a leading "CBMi"/type tag if present, pad for base64url
    for candidate in (token, token[4:] if token.startswith("CBMi") else token):
        with_pad = candidate + "=" * (-len(candidate) % 4)
        try:
            raw = base64.urlsafe_b64decode(with_pad)
        except Exception:
            continue
        found = _URL_RE.search(raw)
        if found:
            real = found.group(0).decode("utf-8", "ignore")
            # trim trailing protobuf bytes that aren't URL-legal
            real = re.split(r"[^\w:/.?=&%#\-]", real, maxsplit=1)[0]
            if real.startswith("http") and _GNEWS_HOST not in real:
                return real, _domain(real)
    return None


async def resolve_via_redirect(url: str, timeout: float = 8.0) -> tuple[str, str] | None:
    """Fallback: follow the redirect to capture the final outlet URL."""
    import httpx

    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            resp = await client.get(url)
        final = str(resp.url)
        if _GNEWS_HOST not in final:
            return final, _domain(final)
    except Exception:
        return None
    return None


async def resolve_batch_via_browser(urls: list[str], budget: int = 12,
                                    concurrency: int = 3) -> dict[str, tuple[str, str]]:
    """Resolve Google-News URLs to the real outlet by loading them in headless
    Chromium (Google's own JS redirects to the article) — the reliable method the
    offline decode + plain redirect can't do. Returns {orig_url: (real_url, domain)}.
    Budgeted + concurrency-capped because each navigation is a real page load."""
    import asyncio

    targets = [u for u in dict.fromkeys(urls) if is_gnews_url(u)][:budget]
    if not targets:
        return {}
    try:
        from playwright.async_api import async_playwright
    except Exception:
        return {}

    out: dict[str, tuple[str, str]] = {}
    sem = asyncio.Semaphore(concurrency)
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(args=["--no-sandbox"])

            async def one(u: str) -> None:
                async with sem:
                    page = await browser.new_page()
                    try:
                        await page.goto(u, wait_until="domcontentloaded", timeout=20000)
                        await page.wait_for_timeout(2500)   # let the JS redirect settle
                        final = page.url
                        if final.startswith("http") and _GNEWS_HOST not in final:
                            out[u] = (final, _domain(final))
                    except Exception:
                        pass
                    finally:
                        with contextlib.suppress(Exception):
                            await page.close()

            await asyncio.gather(*(one(u) for u in targets))
            await browser.close()
    except Exception:
        return out
    return out
