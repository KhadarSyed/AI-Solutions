import pytest

from app.tools.connectors.base import SearchFilters
from app.tools.connectors.keyed import ApifyConnector


@pytest.mark.asyncio
async def test_apify_normalizes_dataset_items(monkeypatch):
    conn = ApifyConnector()

    async def fake_run(actor, query, filters):
        return [{"title": "BeOne wins award", "url": "https://ex.com/a",
                 "description": "body", "loadedUrl": "https://ex.com/a"}]

    monkeypatch.setattr(conn, "_run_actor", fake_run)
    monkeypatch.setattr(
        "app.tools.connectors.keyed.get_settings",
        lambda: type("S", (), {"apify_token": "x", "apify_actor_search": "a/s",
                               "apify_actor_rag": "a/r"})(),
    )
    res = await conn.search(["BeOne"], SearchFilters())
    assert res.articles and res.articles[0].source == "apify"
    assert res.articles[0].publisher_domain == "ex.com"
    assert res.articles[0].medium == "news"


@pytest.mark.asyncio
async def test_apify_isolates_actor_errors(monkeypatch):
    conn = ApifyConnector()

    async def boom(actor, query, filters):
        raise RuntimeError("apify 429")

    monkeypatch.setattr(conn, "_run_actor", boom)
    monkeypatch.setattr(
        "app.tools.connectors.keyed.get_settings",
        lambda: type("S", (), {"apify_token": "x", "apify_actor_search": "a/s",
                               "apify_actor_rag": "a/r"})(),
    )
    res = await conn.search(["BeOne"], SearchFilters())
    assert res.articles == []
    assert any("apify 429" in e for e in res.errors)
