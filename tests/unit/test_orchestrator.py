import pytest

import app.services.ingestion_service as ing
from app.tools.connectors.base import ConnectorResult, RawArticle


class _FakeConn:
    def __init__(self, name, fail=False):
        self.name = name
        self._fail = fail

    def enabled(self):
        return True

    async def search(self, queries, filters):
        if self._fail:
            raise RuntimeError("boom")
        return ConnectorResult(articles=[
            RawArticle(title=f"{q} via {self.name}", url=f"https://{self.name}.com/{q}",
                       original_query=q) for q in queries])


async def _ident(arts, *a, **k):
    return arts


@pytest.mark.asyncio
async def test_fanout_isolates_failing_source_and_stamps_subject(monkeypatch):
    monkeypatch.setattr(ing, "enabled_connectors",
                        lambda: [_FakeConn("ok"), _FakeConn("bad", fail=True)])

    async def _no_persist(*a, **k):
        return None

    monkeypatch.setattr(ing, "_persist_source_file", _no_persist)
    monkeypatch.setattr(ing, "relevancy_filter", _ident)
    plan = [{"name": "Competitors News", "queries": ["Carrier"],
             "subject": None, "subject_per_query": True}]
    stats = await ing.collect(session_id="00000000-0000-0000-0000-000000000001",
                              brand="BeOne", query_groups=plan)
    assert stats["per_source"]["ok"] == 1
    assert any("bad" in e for e in stats["errors"])
