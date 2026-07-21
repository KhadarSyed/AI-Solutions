"""Lexical recall (embedding-independent) — BM25-lite scoring over a fixed corpus."""
import pytest

from app.retrieval import lexical

CORPUS = [
    ("s1", {"id": "A0", "title": "BeOne launches new insulin biosimilar in Europe",
            "content": "The pharmaceutical firm BeOne announced a biosimilar insulin launch "
                       "across European markets, expanding its diabetes portfolio.",
            "is_approved": True, "xai_section": "Brand News", "xai_sentiment": "POS",
            "publisher_name": "Reuters", "publisher_domain": "reuters.com",
            "url": "https://reuters.com/a0", "published_date": "2026-07-10"}),
    ("s1", {"id": "A1", "title": "Local bakery wins dessert award",
            "content": "A neighbourhood bakery took home a prize for its croissants and cakes "
                       "at the county fair this weekend.",
            "is_approved": True, "xai_section": "Industry News", "xai_sentiment": "NEU",
            "publisher_name": "Town Gazette", "publisher_domain": "gazette.example",
            "url": "https://gazette.example/a1", "published_date": "2026-07-11"}),
    ("s1", {"id": "A2", "title": "Diabetes treatment market grows",
            "content": "Analysts say the diabetes treatment and insulin market continues to "
                       "grow as biosimilars enter Europe.",
            "is_approved": True, "xai_section": "Industry News", "xai_sentiment": "NEU",
            "publisher_name": "Pharma Times", "publisher_domain": "pharmatimes.example",
            "url": "https://pharmatimes.example/a2", "published_date": "2026-07-09"}),
    ("s1", {"id": "A3", "title": "Unapproved draft about insulin",
            "content": "insulin insulin insulin biosimilar europe beone",
            "is_approved": False, "xai_section": "Brand News", "xai_sentiment": "POS",
            "publisher_name": "Draft", "publisher_domain": "draft.example",
            "url": "https://draft.example/a3", "published_date": "2026-07-08"}),
]


@pytest.fixture(autouse=True)
def _fixed_corpus(monkeypatch):
    async def fake_articles_for(project_id, session_id):
        return list(CORPUS)

    monkeypatch.setattr(lexical, "_articles_for", fake_articles_for)


async def test_relevant_doc_ranks_first():
    hits = await lexical.recall_topk_lexical(
        project_id="p", query="BeOne insulin biosimilar Europe launch")
    assert hits, "expected recall hits"
    assert hits[0].article_id == "A0"
    # the on-topic industry article also surfaces, the bakery does not lead
    assert "A2" in [h.article_id for h in hits[:3]]


async def test_approved_only_excludes_unapproved():
    hits = await lexical.recall_topk_lexical(
        project_id="p", query="insulin biosimilar", approved_only=True)
    assert "A3" not in [h.article_id for h in hits]


async def test_offtopic_query_returns_no_matches():
    # no query term appears in any approved doc → empty (nothing to hand the reranker)
    hits = await lexical.recall_topk_lexical(
        project_id="p", query="quantum spacecraft propulsion telescope")
    assert hits == []


async def test_hit_shape_matches_vector_path():
    hits = await lexical.recall_topk_lexical(project_id="p", query="insulin biosimilar")
    h = hits[0]
    assert h.meta["publisher"] and h.meta["title"] and h.meta["url"]
    assert h.content_preview.startswith(h.meta["title"])
    assert 0.0 <= h.similarity <= 1.0
