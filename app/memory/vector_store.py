"""Article RAG vector store — article_embeddings table (separate from Mem0's
memory collection). Embedding input per playbook §8: title + first 1000 chars
+ classification signals + entity mentions."""

import uuid
from datetime import date

from sqlalchemy import delete, update

from app.db.base import get_sessionmaker
from app.db.models import ArticleEmbedding
from app.llm_gateway.embeddings import embed
from app.observability.logging import get_logger

log = get_logger(__name__)


def _embedding_input(article: dict) -> str:
    entities = article.get("entities", {}) or {}
    mentions = ", ".join(
        entities.get("brand_of_interest", []) + entities.get("competitors", [])
    )
    return (
        f"{article.get('title', '')}\n{(article.get('content') or '')[:1000]}\n"
        f"sentiment={article.get('xai_sentiment', '')} theme={article.get('xai_theme', '')}\n"
        f"mentions: {mentions}"
    )


def _parse_date(value) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


async def upsert_articles(project_id: str, session_id: str, articles: list[dict]) -> int:
    if not articles:
        return 0
    vectors = await embed([_embedding_input(a) for a in articles])

    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            delete(ArticleEmbedding).where(
                ArticleEmbedding.session_id == uuid.UUID(session_id)
            )
        )
        for article, vector in zip(articles, vectors, strict=True):
            db.add(
                ArticleEmbedding(
                    project_id=uuid.UUID(project_id),
                    session_id=uuid.UUID(session_id),
                    article_id=article["id"],
                    embedding=vector,
                    section=article.get("xai_section"),
                    sentiment=article.get("xai_sentiment"),
                    published_at=_parse_date(article.get("published_date")),
                    is_approved=bool(article.get("is_approved", False)),
                    is_approved_for_monitoring=bool(
                        article.get("is_approved_for_monitoring", False)
                    ),
                    content_preview=(
                        f"{article.get('title', '')} — {(article.get('content') or '')[:800]}"
                    ),
                    meta={
                        "publisher": article.get("publisher_name", ""),
                        "domain": article.get("publisher_domain", ""),
                        "url": article.get("url", ""),
                        "theme": article.get("xai_theme", ""),
                    },
                )
            )
    log.info("vector_store.upserted", session=session_id, n=len(articles))
    return len(articles)


async def sync_flags(
    session_id: str, article_id: str, *, is_approved: bool | None = None,
    is_approved_for_monitoring: bool | None = None,
    section: str | None = None, sentiment: str | None = None,
) -> None:
    values: dict = {}
    if is_approved is not None:
        values["is_approved"] = is_approved
    if is_approved_for_monitoring is not None:
        values["is_approved_for_monitoring"] = is_approved_for_monitoring
    if section is not None:
        values["section"] = section
    if sentiment is not None:
        values["sentiment"] = sentiment
    if not values:
        return
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            update(ArticleEmbedding)
            .where(
                ArticleEmbedding.session_id == uuid.UUID(session_id),
                ArticleEmbedding.article_id == article_id,
            )
            .values(**values)
        )


async def delete_article(session_id: str, article_id: str) -> None:
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(
            delete(ArticleEmbedding).where(
                ArticleEmbedding.session_id == uuid.UUID(session_id),
                ArticleEmbedding.article_id == article_id,
            )
        )
