from typing import Annotated

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import get_settings
from app.db.base import get_db
from app.db.models import GuardrailEvent, LLMCall
from app.security.auth import require_admin

router = APIRouter(tags=["admin"])

DB = Annotated[AsyncSession, Depends(get_db)]


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


@router.get("/admin/llm-calls", dependencies=[Depends(require_admin)])
async def llm_calls(db: DB, limit: int = Query(50, le=500)) -> dict:
    rows = (
        await db.execute(select(LLMCall).order_by(LLMCall.created_at.desc()).limit(limit))
    ).scalars().all()
    totals = (
        await db.execute(
            select(
                func.count(LLMCall.id),
                func.coalesce(func.sum(LLMCall.cost_usd), 0),
                func.coalesce(func.sum(LLMCall.input_tokens + LLMCall.output_tokens), 0),
            )
        )
    ).one()
    return {
        "total_calls": totals[0],
        "total_cost_usd": float(totals[1]),
        "total_tokens": int(totals[2]),
        "calls": [
            {
                "at": r.created_at.isoformat(),
                "provider": r.provider,
                "model": r.model,
                "stage": r.stage,
                "purpose": r.purpose,
                "in_tokens": r.input_tokens,
                "out_tokens": r.output_tokens,
                "cost_usd": float(r.cost_usd),
                "latency_ms": r.latency_ms,
                "cache_hit": r.cache_hit,
                "fallback_used": r.fallback_used,
                "status": r.status,
            }
            for r in rows
        ],
    }


@router.get("/admin/guardrail-events", dependencies=[Depends(require_admin)])
async def guardrail_events(
    db: DB,
    limit: int = Query(50, le=500),
    verdict: str | None = None,
) -> dict:
    q = select(GuardrailEvent).order_by(GuardrailEvent.created_at.desc()).limit(limit)
    if verdict:
        q = q.where(GuardrailEvent.verdict == verdict)
    rows = (await db.execute(q)).scalars().all()
    return {
        "events": [
            {
                "at": r.created_at.isoformat(),
                "request_id": r.request_id,
                "direction": r.direction,
                "check": r.check_name,
                "verdict": r.verdict,
                "details": r.details,
            }
            for r in rows
        ]
    }
