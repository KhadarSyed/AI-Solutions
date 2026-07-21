"""article_embeddings vector(384) for local embeddings backend

Switches the article RAG embedding column from vector(1536) (Azure text-embedding-3-small)
to vector(384) (local fastembed bge-small-en-v1.5). Safe drop+recreate: the table is
populated only by tagging runs and is re-embedded on the next run.

Revision ID: c1f2a3b4d5e6
Revises: bdae5d8014aa
Create Date: 2026-07-21 07:10:00.000000
"""
from collections.abc import Sequence

import sqlalchemy as sa
from pgvector.sqlalchemy import Vector

from alembic import op

revision: str = "c1f2a3b4d5e6"
down_revision: str | None = "bdae5d8014aa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_HNSW = "ix_article_embeddings_hnsw"


def _swap_embedding_dim(new_dim: int, old_dim: int) -> None:
    # HNSW index is dimension-bound → drop, swap the column, recreate.
    op.execute(f"DROP INDEX IF EXISTS {_HNSW}")
    # Re-embedded on the next tagging run, so clearing rows loses nothing durable.
    op.execute("DELETE FROM article_embeddings")
    op.drop_column("article_embeddings", "embedding")
    op.add_column("article_embeddings", sa.Column("embedding", Vector(new_dim), nullable=False))
    op.execute(
        f"CREATE INDEX {_HNSW} ON article_embeddings "
        f"USING hnsw (embedding vector_cosine_ops)"
    )


def upgrade() -> None:
    _swap_embedding_dim(384, 1536)


def downgrade() -> None:
    _swap_embedding_dim(1536, 384)
