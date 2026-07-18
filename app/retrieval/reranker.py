"""Cross-encoder reranking — FlashRank, fully local (ONNX, no GPU, no API)."""

from functools import lru_cache

from app.observability.logging import get_logger

log = get_logger(__name__)


@lru_cache
def _ranker():
    from flashrank import Ranker

    # ~34MB model, downloaded once to the local cache on first use
    return Ranker(model_name="ms-marco-MiniLM-L-12-v2")


def rerank(query: str, passages: list[dict], top_k: int = 10) -> list[dict]:
    """passages: [{"id": ..., "text": ..., "meta": {...}}] → top_k sorted with 'score'."""
    if not passages:
        return []
    from flashrank import RerankRequest

    results = _ranker().rerank(RerankRequest(query=query, passages=passages))
    return list(results)[:top_k]
