"""Wires ChannelAdapters into the Notifier and runs the inbound poll loop."""

import asyncio
import contextlib

import redis.asyncio as aioredis

from app.channels.base import OutboundMessage
from app.channels.csv_export import COLLECTED_COLUMNS  # noqa: F401 (re-export convenience)
from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)

INBOUND_INTERVAL = 30
DEDUPE_TTL = 7 * 24 * 3600


def _adapters() -> list:
    from app.channels.email_imap import EmailAdapter
    from app.channels.teams_mcp import TeamsMcpAdapter

    return [a for a in (EmailAdapter(), TeamsMcpAdapter()) if a.enabled()]


def register_channel_adapters() -> None:
    """Give the Notifier a per-channel delivery function for gate CSVs."""
    from app.artifacts.factory import get_artifact_store
    from app.channels import notifier

    async def deliver(channel: str, adapter, state, gate, csv_key, message):
        store = get_artifact_store()
        csv_bytes = await store.get_bytes(csv_key)
        name = csv_key.rsplit("/", 1)[-1]
        await adapter.send(
            state.get("origin_address", {}),
            OutboundMessage(subject=f"Gate {gate} — action needed", text=message,
                            attachments=[(name, csv_bytes, "text/csv")]),
        )

    for adapter in _adapters():
        base = adapter.channel  # "email" | "teams"

        def make(adapter):
            async def _fn(state, gate, csv_key, message):
                await deliver(adapter.channel, adapter, state, gate, csv_key, message)
            return _fn

        fn = make(adapter)
        # origin_channel values map onto adapters
        if base == "email":
            notifier.register_adapter("email", fn)
        else:
            notifier.register_adapter("teams_chat", fn)
            notifier.register_adapter("teams_channel", fn)
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

    r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
    log.info("channels.inbound_started", every=INBOUND_INTERVAL)
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
                        await handle_inbound(inbound)
            ticks += 1
            if ticks % 60 == 0:  # ~every 30 min: keep Teams subscriptions alive
                for adapter in adapters:
                    if hasattr(adapter, "renew"):
                        await adapter.renew()
        await asyncio.sleep(INBOUND_INTERVAL)
