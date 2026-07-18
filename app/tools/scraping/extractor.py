"""Body extraction — trafilatura (newspaper3k is unmaintained and breaks on py3.12)."""

import trafilatura


def extract_body(html: str, url: str | None = None) -> str:
    if not html:
        return ""
    text = trafilatura.extract(html, url=url, include_comments=False, favor_precision=True)
    return text or ""


def extract_metadata(html: str, url: str | None = None) -> dict:
    meta = trafilatura.extract_metadata(html, default_url=url)
    if meta is None:
        return {}
    return {
        "author": meta.author or "",
        "date": meta.date or "",
        "sitename": meta.sitename or "",
        "title": meta.title or "",
    }
