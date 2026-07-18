"""Scheduled daily runs — a 60s asyncio loop; timezone-aware due-ness; Redis
SET NX EX lock per query (TTL self-heals crashed runs)."""

import asyncio
import contextlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import redis.asyncio as aioredis
from sqlalchemy import select

from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import GeneratedQuery
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger

log = get_logger(__name__)

INTERVAL_SECONDS = 60
LOCK_TTL = 3600


def _due(q: GeneratedQuery, now_utc: datetime) -> bool:
    if not q.is_active or q.schedule_time is None:
        return False
    try:
        local_now = now_utc.astimezone(ZoneInfo(q.timezone or "UTC"))
    except Exception:
        local_now = now_utc
    already_ran = q.last_run_date == local_now.date()
    return (not already_ran) and local_now.time() >= q.schedule_time


async def _run_due_query(q: GeneratedQuery) -> None:
    from app.orchestration.run_manager import get_run_manager

    async with get_sessionmaker()() as db, db.begin():
        session_row = SessionRow(
            project_id=q.project_id,
            config={"brand": q.brand, "query_groups": q.query_groups,
                    "competitors": q.competitors},
        )
        db.add(session_row)
        await db.flush()
        sid = str(session_row.id)

        fresh = await db.get(GeneratedQuery, q.id)
        fresh.last_run_date = datetime.now(UTC).astimezone(
            ZoneInfo(q.timezone or "UTC")
        ).date()
        fresh.last_session_id = session_row.id

    await get_run_manager().start(
        graph_name="pipeline",
        input_state={"project_id": str(q.project_id), "session_id": sid},
        session_id=sid,
        origin_channel="scheduler",
    )
    log.info("scheduler.run_started", query=str(q.id), session=sid)


async def tick() -> int:
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    started = 0
    try:
        now = datetime.now(UTC)
        async with get_sessionmaker()() as db:
            queries = (
                (await db.execute(select(GeneratedQuery)
                                  .where(GeneratedQuery.is_active.is_(True)))).scalars().all()
            )
        for q in queries:
            if not _due(q, now):
                continue
            got_lock = await r.set(f"sched:inflight:{q.id}", "1", nx=True, ex=LOCK_TTL)
            if not got_lock:
                continue
            try:
                await _run_due_query(q)
                started += 1
            except Exception as exc:
                log.error("scheduler.run_failed", query=str(q.id), error=str(exc)[:200])
                await r.delete(f"sched:inflight:{q.id}")
    finally:
        with contextlib.suppress(Exception):
            await r.aclose()
    return started


async def scheduler_loop() -> None:
    log.info("scheduler.started", interval=INTERVAL_SECONDS)
    while True:
        with contextlib.suppress(asyncio.CancelledError):
            try:
                await tick()
            except Exception as exc:
                log.error("scheduler.tick_failed", error=str(exc)[:200])
        await asyncio.sleep(INTERVAL_SECONDS)
