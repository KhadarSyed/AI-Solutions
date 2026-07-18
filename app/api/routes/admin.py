import redis.asyncio as aioredis
from fastapi import APIRouter
from sqlalchemy import text

from app.config.settings import get_settings

router = APIRouter(tags=["admin"])


@router.get("/health")
async def health() -> dict:
    settings = get_settings()
    status: dict[str, str] = {}

    try:
        from app.db.base import get_engine

        async with get_engine().connect() as conn:
            await conn.execute(text("SELECT 1"))
        status["db"] = "up"
    except Exception as exc:  # pragma: no cover
        status["db"] = f"down: {type(exc).__name__}"

    try:
        r = aioredis.from_url(settings.redis_url)
        await r.ping()
        await r.aclose()
        status["redis"] = "up"
    except Exception as exc:  # pragma: no cover
        status["redis"] = f"down: {type(exc).__name__}"

    try:
        from neo4j import AsyncGraphDatabase

        driver = AsyncGraphDatabase.driver(
            settings.neo4j_uri, auth=(settings.neo4j_username, settings.neo4j_password)
        )
        await driver.verify_connectivity()
        await driver.close()
        status["neo4j"] = "up"
    except Exception as exc:  # pragma: no cover
        status["neo4j"] = f"down: {type(exc).__name__}"

    overall = "ok" if all(v == "up" for v in status.values()) else "degraded"
    return {"status": overall, **status}
