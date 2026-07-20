"""Body extraction — trafilatura (newspaper3k is unmaintained and breaks on py3.12)."""

import contextlib
import json
import re

import trafilatura

_META_AUTHOR = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:author|article:author)["\'][^>]+content=["\']([^"\']+)',
    re.I)
_META_AUTHOR2 = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+(?:name|property)=["\'](?:author|article:author)["\']',
    re.I)
_REL_AUTHOR = re.compile(r'<a[^>]+rel=["\']author["\'][^>]*>([^<]+)</a>', re.I)
_BYLINE_CLASS = re.compile(r'class=["\'][^"\']*byline[^"\']*["\'][^>]*>(.*?)</', re.I | re.S)
_LDJSON = re.compile(r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
                     re.I | re.S)
_BY_PREFIX = re.compile(r"^\s*(?:by|written by|posted by)\s+", re.I)


def _clean_author(s: str) -> str:
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = _BY_PREFIX.sub("", s.strip())
    s = re.sub(r"\s+", " ", s).strip(" ,|·—-")
    if not s or len(s) > 80 or "@" in s or "http" in s.lower():
        return ""
    return s


def _author_from_ldjson(html: str) -> str:
    for m in _LDJSON.finditer(html or ""):
        with contextlib.suppress(Exception):
            data = json.loads(m.group(1))
            for obj in (data if isinstance(data, list) else [data]):
                a = obj.get("author") if isinstance(obj, dict) else None
                if isinstance(a, dict) and a.get("name"):
                    return str(a["name"])
                if isinstance(a, list) and a and isinstance(a[0], dict) and a[0].get("name"):
                    return str(a[0]["name"])
                if isinstance(a, str) and a:
                    return a
    return ""


def extract_byline(html: str) -> str:
    """Best byline from an article page: meta author → JSON-LD → rel=author →
    a .byline element. Returns '' when nothing clean is found."""
    html = html or ""
    for rx in (_META_AUTHOR, _META_AUTHOR2, _REL_AUTHOR):
        m = rx.search(html)
        if m:
            cleaned = _clean_author(m.group(1))
            if cleaned:
                return cleaned
    cleaned = _clean_author(_author_from_ldjson(html))
    if cleaned:
        return cleaned
    m = _BYLINE_CLASS.search(html)
    if m:
        cleaned = _clean_author(m.group(1))
        if cleaned:
            return cleaned
    return ""


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
