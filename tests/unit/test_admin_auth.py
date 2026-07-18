import httpx

from app.main import create_app


async def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=create_app()), base_url="http://test"
    )


async def test_admin_requires_key():
    async with await _client() as c:
        r = await c.get("/admin/llm-calls")
    assert r.status_code == 401


async def test_default_admin_key_is_refused():
    # with ADMIN_API_KEY left at its default, even a "correct" key must be rejected
    async with await _client() as c:
        r = await c.get("/admin/llm-calls", headers={"X-API-Key": "change-me-admin-key"})
    assert r.status_code == 403
