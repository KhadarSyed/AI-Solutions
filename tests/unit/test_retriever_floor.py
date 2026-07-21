"""retrieve() relevance floor — the anti-hallucination gate that splits reranked
candidates into answerable (>= floor) vs. closest-below (for the rephrase hint)."""
import pytest

from app.retrieval import retriever
from app.retrieval.bi_encoder import RecallHit


def _hit(aid: str, title: str) -> RecallHit:
    return RecallHit(
        article_id=aid, session_id="s1", content_preview=f"{title} — body text",
        section="Brand News", sentiment="POS", published_at=None, similarity=0.5,
        meta={"publisher": "Reuters", "title": title, "url": "u", "domain": "d"})


@pytest.fixture(autouse=True)
def _stub_recall(monkeypatch):
    async def fake_recall(**kw):
        return [_hit("A0", "strong match"), _hit("A1", "weak match"), _hit("A2", "weakest")]

    monkeypatch.setattr(retriever, "_recall", fake_recall)


def _rerank_with(scores):
    def fake(query, passages, top_k=10):
        out = [{**p, "score": scores[p["id"]]} for p in passages]
        return sorted(out, key=lambda r: r["score"], reverse=True)[:top_k]
    return fake


async def test_floor_splits_above_and_below(monkeypatch):
    monkeypatch.setattr(retriever, "rerank", _rerank_with({"A0": 0.9, "A1": 0.02, "A2": 0.01}))
    res = await retriever.retrieve(project_id="p", query="q")
    assert [a.article_id for a in res.articles] == ["A0"]
    assert [a.article_id for a in res.closest] == ["A1", "A2"]
    assert bool(res) is True


async def test_all_below_floor_is_empty(monkeypatch):
    monkeypatch.setattr(retriever, "rerank", _rerank_with({"A0": 0.05, "A1": 0.02, "A2": 0.01}))
    res = await retriever.retrieve(project_id="p", query="q")
    assert res.articles == []
    assert [a.article_id for a in res.closest] == ["A0", "A1", "A2"]
    assert bool(res) is False
    assert res.closest[0].headline == "strong match"
