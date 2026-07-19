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
    """Map each origin_channel value onto its delivering adapter.

    When teams-mcp is enabled it also serves the `email` origin via Graph
    mail_send from the agent's real mailbox — preferred over the greenmail SMTP
    adapter, which stays only as the offline fallback."""
    from app.channels import notifier

    adapters = _adapters()
    teams = next((a for a in adapters if a.channel == "teams"), None)
    email = next((a for a in adapters if a.channel == "email"), None)

    if teams is not None:
        notifier.register_adapter("teams_chat", teams)
        notifier.register_adapter("teams_channel", teams)
        notifier.register_adapter("email", teams)   # real mailbox via Graph
    elif email is not None:
        notifier.register_adapter("email", email)   # greenmail fallback
    log.info("channels.registered",
             email="graph" if teams else ("smtp" if email else "none"),
             teams=bool(teams))


async def inbound_loop() -> None:
    adapters = _adapters()
    if not adapters:
        log.info("channels.inbound_disabled")
        return
    from app.agents.email_agent import handle_inbound

    interval = get_settings().inbound_poll_seconds
    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)

    # At boot: mint/verify the token and (re)activate the subscription up front,
    # so triggering by email or Teams works from the very first tick — not lazily
    # on first use. A Redis marker (refreshed by the renew loop) lets us skip the
    # slow re-subscribe across restarts; we still verify the token every boot.
    SUB_TTL = 3300  # 55 min; renew loop refreshes it well before expiry
    for adapter in adapters:
        if hasattr(adapter, "bootstrap"):
            has_sub = bool(await r.get(f"sub:active:{adapter.channel}"))
            ok = await adapter.bootstrap(resubscribe=not has_sub)
            if ok:
                await r.set(f"sub:active:{adapter.channel}", "1", ex=SUB_TTL)
                log.info("channels.bootstrapped", channel=adapter.channel,
                         resubscribed=not has_sub)
        elif hasattr(adapter, "subscribe"):
            if await r.get(f"sub:active:{adapter.channel}"):
                log.info("channels.subscription_reused", channel=adapter.channel)
            elif await adapter.subscribe():
                await r.set(f"sub:active:{adapter.channel}", "1", ex=SUB_TTL)

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
                        await r.set(f"sub:active:{adapter.channel}", "1", ex=SUB_TTL)
        await asyncio.sleep(interval)
