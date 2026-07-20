import asyncio

import pytest

import app.services.ingestion_service as ing
from app.orchestration.context import current_run_id
from app.tools.connectors.base import ConnectorResult, RawArticle


class _EchoConn:
    name = "echo"

    def enabled(self):
        return True

    async def search(self, queries, filters):
        await asyncio.sleep(0.01)  # force interleaving of the two runs
        return ConnectorResult(articles=[
            RawArticle(title=q, url=f"https://echo.com/{q}", original_query=q)
            for q in queries])


class _FakeBus:
    def __init__(self):
        self.run_ids = set()

    async def emit(self, run_id, event_type, node=None, payload=None):
        self.run_ids.add(run_id)
        return 1


@pytest.mark.asyncio
async def test_two_runs_do_not_cross_contaminate(monkeypatch):
    monkeypatch.setattr(ing, "enabled_connectors", lambda: [_EchoConn()])
    captured: dict = {}

    async def _persist(session_id, payload):
        captured[session_id] = payload

    async def _ident(arts, *a, **k):
        return arts

    bus = _FakeBus()
    monkeypatch.setattr(ing, "_persist_source_file", _persist)
    monkeypatch.setattr(ing, "relevancy_filter", _ident)
    monkeypatch.setattr("app.orchestration.events.get_event_bus", lambda: bus)

    async def run(run_id, session_id, brand, comp):
        current_run_id.set(run_id)
        plan = [{"name": "Competitors News", "queries": [comp],
                 "subject": None, "subject_per_query": True}]
        await ing.collect(session_id=session_id, brand=brand,
                          query_groups=plan, run_id=run_id)

    await asyncio.gather(
        run("r1", "11111111-1111-1111-1111-111111111111", "BeOne", "Carrier"),
        run("r2", "22222222-2222-2222-2222-222222222222", "Trane", "Daikin"),
    )
    a1 = captured["11111111-1111-1111-1111-111111111111"]["articles"]
    a2 = captured["22222222-2222-2222-2222-222222222222"]["articles"]
    assert {a["subject_brand"] for a in a1} == {"Carrier"}
    assert {a["subject_brand"] for a in a2} == {"Daikin"}
    assert bus.run_ids == {"r1", "r2"}
