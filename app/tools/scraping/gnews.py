"""Decode Google News redirect URLs to the real article URL + publisher domain.

Google News RSS links look like
  https://news.google.com/rss/articles/CBMi<base64url-payload>...
The payload is a protobuf-ish blob; the article URL appears as a UTF-8 run
inside it. We extract the first http(s) URL we can find; if that fails we fall
back to following the redirect. Best-effort — returns None when we can't resolve.
"""
import base64
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
