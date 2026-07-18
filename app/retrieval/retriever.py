"""Retrieval pipeline: bi-encoder recall (pgvector, top-50) →
cross-encoder rerank (FlashRank, top-10) → citation-ready context."""

from dataclasses import dataclass
from uuid import UUID

from app.retrieval.bi_encoder import RecallHit, recall_topk
from app.retrieval.reranker import rerank


@dataclass
class RetrievedArticle:
    article_id: str
    text: str
    rerank_score: float
    similarity: float
    section: str | None
    sentiment: str | None
    meta: dict


async def retrieve(
    *,
    project_id: UUID | str,
    query: str,
    k_recall: int = 50,
    k_final: int = 10,
    approved_only: bool = True,
    monitoring_audience: bool = False,
    **filters,
) -> list[RetrievedArticle]:
    hits: list[RecallHit] = await recall_topk(
        project_id=project_id,
        query=query,
        k=k_recall,
        approved_only=approved_only,
        monitoring_audience=monitoring_audience,
        **filters,
    )
    if not hits:
        return []

    by_id = {h.article_id: h for h in hits}
    passages = [{"id": h.article_id, "text": h.content_preview, "meta": h.meta} for h in hits]
    ranked = rerank(query, passages, top_k=k_final)

    out = []
    for r in ranked:
        h = by_id[r["id"]]
        out.append(
            RetrievedArticle(
                article_id=h.article_id,
                text=h.content_preview,
                rerank_score=float(r["score"]),
                similarity=h.similarity,
                section=h.section,
                sentiment=h.sentiment,
                meta=h.meta,
            )
        )
    return out


def to_context_block(articles: list[RetrievedArticle]) -> str:
    """Render retrieved articles as a grounded-generation context with citation ids."""
    if not articles:
        return "No relevant articles found in the approved corpus."
    lines = []
    for a in articles:
        tag = f"[{a.article_id}]"
        extras = " · ".join(x for x in [a.section, a.sentiment] if x)
        lines.append(f"{tag} ({extras}) {a.text[:600]}")
    return (
        "Answer STRICTLY from these articles and cite them by id like [A12]:\n" + "\n\n".join(lines)
    )
