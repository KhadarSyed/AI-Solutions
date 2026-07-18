"""EventBus — every node transition, memory op, guardrail event, heal step and
notification becomes an append-only run_events row AND a Redis pub/sub message,
so /ws/runs/{run_id} can replay history and follow live."""

import asyncio
import json
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from functools import lru_cache

import redis.asyncio as aioredis
from sqlalchemy import func, select

from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import RunEvent
from app.observability.logging import get_logger

log = get_logger(__name__)


def channel_for(run_id: str | uuid.UUID) -> str:
    return f"run:{run_id}"


class EventBus:
    def __init__(self):
        self._locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self._redis: aioredis.Redis | None = None

    def redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        return self._redis

    async def emit(
        self,
        run_id: str | uuid.UUID,
        event_type: str,
        node: str | None = None,
        payload: dict | None = None,
    ) -> int:
        rid = str(run_id)
        payload = payload or {}
        async with self._locks[rid], get_sessionmaker()() as session, session.begin():
            next_seq = (
                await session.execute(
                    select(func.coalesce(func.max(RunEvent.seq), 0) + 1).where(
                        RunEvent.run_id == run_id
                    )
                )
            ).scalar_one()
            session.add(
                RunEvent(
                    run_id=run_id, seq=next_seq, event_type=event_type,
                    node=node, payload=payload,
                )
            )
        message = json.dumps(
            {
                "run_id": rid, "seq": next_seq, "event_type": event_type,
                "node": node, "payload": payload,
                "at": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False, default=str,
        )
        try:
            await self.redis().publish(channel_for(rid), message)
        except Exception as exc:  # live stream is best-effort; the row is the record
            log.warning("eventbus.publish_failed", error=str(exc))
        return next_seq

    async def replay(self, run_id: str | uuid.UUID, from_seq: int = 0) -> list[dict]:
        async with get_sessionmaker()() as session:
            rows = (
                await session.execute(
                    select(RunEvent)
                    .where(RunEvent.run_id == run_id, RunEvent.seq > from_seq)
                    .order_by(RunEvent.seq)
                )
            ).scalars().all()
        return [
            {
                "run_id": str(r.run_id), "seq": r.seq, "event_type": r.event_type,
                "node": r.node, "payload": r.payload, "at": r.created_at.isoformat(),
            }
            for r in rows
        ]


@lru_cache
def get_event_bus() -> EventBus:
    return EventBus()
