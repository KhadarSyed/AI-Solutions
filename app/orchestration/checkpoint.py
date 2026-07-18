"""Postgres checkpointer for LangGraph — durable graph state, pause/resume/recovery.

Its tables are created by `.setup()` (prefix `checkpoint*`), deliberately excluded
from Alembic autogenerate (see alembic/env.py FOREIGN_TABLE_PREFIXES).
"""

from contextlib import asynccontextmanager

from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from app.config.settings import get_settings


def _dsn() -> str:
    # langgraph's saver uses psycopg — plain postgresql:// scheme
    return get_settings().database_url.replace("postgresql+asyncpg", "postgresql")


@asynccontextmanager
async def checkpointer():
    async with AsyncPostgresSaver.from_conn_string(_dsn()) as saver:
        yield saver


async def setup_checkpointer_tables() -> None:
    """One-time (idempotent) creation of the checkpointer's own tables."""
    async with checkpointer() as saver:
        await saver.setup()
