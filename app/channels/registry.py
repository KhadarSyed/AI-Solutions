"""Wires ChannelAdapters into the Notifier and runs the inbound poll loop."""

import asyncio
import contextlib

import redis.asyncio as aioredis

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

DEDUPE_TTL = 7 * 24 * 3600


def _adapters() -> list:
    from app.channels.email_imap import EmailAdapter
    from app.channels.teams_mcp import TeamsMcpAdapter

    return [a for a in (EmailAdapter(), TeamsMcpAdapter()) if a.enabled()]


def register_channel_adapters() -> None:
    """Map each origin_channel value onto its delivering adapter."""
    from app.channels import notifier

    for adapter in _adapters():
        if adapter.channel == "email":
            notifier.register_adapter("email", adapter)
        else:  # one Teams adapter serves both chat and channel origins
            notifier.register_adapter("teams_chat", adapter)
            notifier.register_adapter("teams_channel", adapter)
    log.info("channels.registered", adapters=[a.channel for a in _adapters()])


async def inbound_loop() -> None:
    adapters = _adapters()
    if not adapters:
        log.info("channels.inbound_disabled")
        return
    from app.agents.email_agent import handle_inbound

    # Teams needs an active mention subscription before polling returns anything.
    for adapter in adapters:
        if hasattr(adapter, "subscribe"):
            await adapter.subscribe()

    interval = get_settings().inbound_poll_seconds
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    log.info("channels.inbound_started", every=interval)

    inflight: set[asyncio.Task] = set()

    async def _dispatch(inbound) -> None:
        # each request is handled concurrently so many mentions/emails progress
        # in parallel; runs themselves are governed by the RunManager semaphore
        with contextlib.suppress(Exception):
            await handle_inbound(inbound)

    ticks = 0
    while True:
        with contextlib.suppress(asyncio.CancelledError):
            for adapter in adapters:
                with contextlib.suppress(Exception):
                    async for inbound in adapter.poll_inbound():
                        if inbound.raw_id:
                            fresh = await r.set(f"inbound:{inbound.raw_id}", "1",
                                                nx=True, ex=DEDUPE_TTL)
                            if not fresh:
                                continue
                        task = asyncio.create_task(_dispatch(inbound))
                        inflight.add(task)
                        task.add_done_callback(inflight.discard)
            ticks += 1
            if ticks % max(1, 1800 // interval) == 0:  # ~every 30 min: keep subs alive
                for adapter in adapters:
                    if hasattr(adapter, "renew"):
                        await adapter.renew()
        await asyncio.sleep(interval)
