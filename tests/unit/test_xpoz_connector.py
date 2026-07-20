import pytest

from app.tools.connectors.base import SearchFilters
from app.tools.connectors.xpoz import (
    XPozConnector,
    _reddit_to_article,
    _tiktok_to_article,
)


def test_reddit_normalization():
    a = _reddit_to_article({"title": "BeOne buzz", "selftext": "body",
        "permalink": "/r/x/abc", "author": "u1", "subreddit": "x"}, "BeOne")
    assert a.medium == "social" and a.source == "reddit"
    assert a.publisher_domain == "reddit.com" and a.author == "u1"
    assert a.subject_brand == "BeOne" and "reddit.com" in a.url


def test_tiktok_normalization():
    a = _tiktok_to_article({"desc": "BeOne clip", "id": "123",
        "author": {"uniqueId": "creator"},
        "webVideoUrl": "https://tiktok.com/@c/video/123"}, "BeOne")
    assert a.medium == "social" and a.source == "tiktok" and a.author == "@creator"


@pytest.mark.asyncio
async def test_search_disabled_without_key(monkeypatch):
    monkeypatch.setattr("app.tools.connectors.xpoz.get_settings",
                        lambda: type("S", (), {"xpoz_api_key": "", "xpoz_mcp_url": "u"})())
    assert XPozConnector().enabled() is False


@pytest.mark.asyncio
async def test_search_normalizes_and_isolates(monkeypatch):
    conn = XPozConnector()

    async def fake_call(tool, args):
        if "Reddit" in tool:
            return [{"title": "BeOne on reddit", "permalink": "/r/x/1",
                     "author": "ru", "subreddit": "x"}]
        raise RuntimeError("tiktok quota")

    monkeypatch.setattr(conn, "_call", fake_call)
    res = await conn.search(["BeOne"], SearchFilters())
    assert len(res.articles) == 1 and res.articles[0].source == "reddit"
    assert any("tiktok quota" in e for e in res.errors)
