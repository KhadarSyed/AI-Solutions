"""data_agent empty-corpus refusal — when RAG clears nothing above the floor and there is
no other grounding, the answer LLM must NOT run (no path to invention); the user gets a
deterministic refusal plus the closest headlines."""
import pytest

from app.agents import data_agent
from app.orchestration.brain import Route, RouteDecision
from app.retrieval.retriever import RetrievalResult, RetrievedArticle


def _closest():
    return [RetrievedArticle(article_id="A9", text="Nearby topic — body", rerank_score=0.03,
                             similarity=0.2, section=None, sentiment=None,
                             meta={"title": "Nearby topic headline"})]


class _ExplodingAgent:
    """Any construction means the code reached the answer LLM — which must not happen."""
    def __init__(self, *a, **k):
        raise AssertionError("answer LLM must not be invoked on the empty-corpus path")


@pytest.fixture
def _rag_only(monkeypatch):
    async def fake_route(query, *, request_id=None):
        return RouteDecision(routes=[Route.RAG], reason="test", confidence=0.9, search_query=query)

    async def fake_brand(session_id):
        return "BeOne"

    monkeypatch.setattr("app.orchestration.brain.route", fake_route)
    monkeypatch.setattr(data_agent, "_session_brand", fake_brand)


async def _collect(gen):
    return [ev async for ev in gen]


async def test_empty_corpus_refuses_without_llm(monkeypatch, _rag_only):
    async def fake_retrieve(**kw):
        return RetrievalResult(articles=[], closest=_closest())

    monkeypatch.setattr(data_agent, "retrieve", fake_retrieve)
    monkeypatch.setattr(data_agent, "GuardedAgent", _ExplodingAgent)

    events = await _collect(data_agent._question_flow("p", "s", "what about pricing?", "rid"))
    answers = [e for e in events if e["event"] == "answer"]
    assert len(answers) == 1
    assert answers[0]["data"]["refused"] is True
    assert "don't have coverage" in answers[0]["data"]["text"]
    assert "Nearby topic headline" in answers[0]["data"]["text"]
    assert "BeOne" in answers[0]["data"]["text"]
    # retrieval event reports zero + closest headlines
    ret = [e for e in events if e["event"] == "retrieval"][0]
    assert ret["data"]["count"] == 0
    assert ret["data"]["closest"] == ["Nearby topic headline"]


async def test_refusal_text_is_deterministic():
    txt = data_agent._refusal_text("BeOne", _closest())
    assert "analyzed BeOne articles" in txt
    assert "Nearby topic headline" in txt
    # no brand → generic phrasing, still no invention
    assert "analyzed coverage" in data_agent._refusal_text("", [])
