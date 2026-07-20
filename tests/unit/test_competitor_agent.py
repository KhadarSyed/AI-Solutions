import pytest

from app.agents import competitor_agent as ca


async def _empty_async(*a, **k):
    return []


@pytest.mark.asyncio
async def test_override_wins_and_caps_at_5(monkeypatch):
    called = {"research": False}

    async def _no_research(*a, **k):
        called["research"] = True
        return []

    monkeypatch.setattr(ca, "_research", _no_research)
    out = await ca.resolve_competitors("BeOne", "p1",
                                       override=["A", "B", "C", "D", "E", "F"])
    assert out == ["A", "B", "C", "D", "E"]
    assert called["research"] is False


@pytest.mark.asyncio
async def test_auto_research_when_no_override(monkeypatch):
    async def _research(brand, project_id):
        return ["Carrier", "Daikin", "Trane", "Lennox", "Johnson Controls"]

    monkeypatch.setattr(ca, "_research", _research)
    monkeypatch.setattr(ca, "_from_project", _empty_async)
    monkeypatch.setattr(ca, "_from_memory", _empty_async)
    monkeypatch.setattr(ca, "_cache", _empty_async)
    out = await ca.resolve_competitors("BeOne", "p1")
    assert out[0] == "Carrier"
    assert len(out) == 5


@pytest.mark.asyncio
async def test_research_failure_returns_empty(monkeypatch):
    async def _boom(brand, project_id):
        raise RuntimeError("no network")

    monkeypatch.setattr(ca, "_research", _boom)
    monkeypatch.setattr(ca, "_from_project", _empty_async)
    monkeypatch.setattr(ca, "_from_memory", _empty_async)
    out = await ca.resolve_competitors("BeOne", "p1")
    assert out == []
