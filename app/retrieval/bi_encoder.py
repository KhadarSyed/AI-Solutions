"""Bi-encoder recall — pgvector cosine top-K over article embeddings."""

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from sqlalchemy import select

from app.db.base import get_sessionmaker
from app.db.models import ArticleEmbedding
from app.llm_gateway.embeddings import embed_one


@dataclass
class RecallHit:
    article_id: str
    session_id: str
    content_preview: str
    section: str | None
    sentiment: str | None
    published_at: date | None
    similarity: float
    meta: dict


async def recall_topk(
    *,
    project_id: UUID | str,
    query: str,
    k: int = 50,
    approved_only: bool = True,
    session_id: UUID | str | None = None,
    section: str | None = None,
    sentiment: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    monitoring_audience: bool = False,
) -> list[RecallHit]:
    qvec = await embed_one(query)
    dist = ArticleEmbedding.embedding.cosine_distance(qvec)

    stmt = (
        select(ArticleEmbedding, dist.label("distance"))
        .where(ArticleEmbedding.project_id == project_id)
        .order_by(dist)
        .limit(k)
    )
    if approved_only:
        flag = (
            ArticleEmbedding.is_approved_for_monitoring
            if monitoring_audience
            else ArticleEmbedding.is_approved
        )
        stmt = stmt.where(flag.is_(True))
    if session_id:
        stmt = stmt.where(ArticleEmbedding.session_id == session_id)
    if section:
        stmt = stmt.where(ArticleEmbedding.section == section)
    if sentiment:
        stmt = stmt.where(ArticleEmbedding.sentiment == sentiment)
    if date_from:
        stmt = stmt.where(ArticleEmbedding.published_at >= date_from)
    if date_to:
        stmt = stmt.where(ArticleEmbedding.published_at <= date_to)

    async with get_sessionmaker()() as session:
        rows = (await session.execute(stmt)).all()

    return [
        RecallHit(
            article_id=r.ArticleEmbedding.article_id,
            session_id=str(r.ArticleEmbedding.session_id),
            content_preview=r.ArticleEmbedding.content_preview or "",
            section=r.ArticleEmbedding.section,
            sentiment=r.ArticleEmbedding.sentiment,
            published_at=r.ArticleEmbedding.published_at,
            similarity=1.0 - float(r.distance),
            meta=r.ArticleEmbedding.meta,
        )
        for r in rows
    ]
