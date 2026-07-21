"""Retrieval pipeline: recall (pgvector top-50, or the lexical fallback when embeddings
are unavailable) → cross-encoder rerank (FlashRank, top-10) → relevance floor →
citation-ready context.

The floor is the anti-hallucination gate: anything the reranker scores below
`retrieval_min_rerank` is dropped from the answerable set and surfaced only as a
"closest match" hint, so the chat can refuse-and-suggest rather than invent."""

from dataclasses import dataclass, field
from uuid import UUID

from app.config.settings import get_settings
from app.llm_gateway.embeddings import embeddings_available
from app.observability.logging import get_logger
from app.retrieval.bi_encoder import RecallHit, recall_topk
from app.retrieval.lexical import recall_topk_lexical
from app.retrieval.reranker import rerank

log = get_logger(__name__)


@dataclass
class RetrievedArticle:
    article_id: str
    text: str
    rerank_score: float
    similarity: float
    section: str | None
    sentiment: str | None
    meta: dict

    @property
    def headline(self) -> str:
        return self.meta.get("title") or self.text.split(" — ", 1)[0][:120]


@dataclass
class RetrievalResult:
    articles: list[RetrievedArticle] = field(default_factory=list)  # at/above the floor
    closest: list[RetrievedArticle] = field(default_factory=list)   # top few below the floor

    def __bool__(self) -> bool:
        return bool(self.articles)


async def _recall(
    *, project_id: UUID | str, query: str, k: int, approved_only: bool, **filters
) -> list[RecallHit]:
    """Vector recall when embeddings are live and it returns anything; else lexical."""
    if await embeddings_available():
        try:
            hits = await recall_topk(
                project_id=project_id, query=query, k=k, approved_only=approved_only, **filters
            )
            if hits:
                return hits
        except Exception as exc:
            log.info("retrieve.vector_failed", error=str(exc)[:160])
    return await recall_topk_lexical(
        project_id=project_id, query=query, k=k, approved_only=approved_only, **filters
    )


async def retrieve(
    *,
    project_id: UUID | str,
    query: str,
    k_recall: int = 50,
    k_final: int = 10,
    approved_only: bool = True,
    monitoring_audience: bool = False,
    **filters,
) -> RetrievalResult:
    hits: list[RecallHit] = await _recall(
        project_id=project_id,
        query=query,
        k=k_recall,
        approved_only=approved_only,
        monitoring_audience=monitoring_audience,
        **filters,
    )
    if not hits:
        return RetrievalResult()

    by_id = {h.article_id: h for h in hits}
    passages = [{"id": h.article_id, "text": h.content_preview, "meta": h.meta} for h in hits]
    ranked = rerank(query, passages, top_k=k_final)

    floor = get_settings().retrieval_min_rerank
    above: list[RetrievedArticle] = []
    below: list[RetrievedArticle] = []
    for r in ranked:
        h = by_id[r["id"]]
        art = RetrievedArticle(
            article_id=h.article_id,
            text=h.content_preview,
            rerank_score=float(r["score"]),
            similarity=h.similarity,
            section=h.section,
            sentiment=h.sentiment,
            meta=h.meta,
        )
        (above if art.rerank_score >= floor else below).append(art)

    return RetrievalResult(articles=above, closest=below[:3])


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
