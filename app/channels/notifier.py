"""Channel-aware HITL notifications — always to the run's ORIGIN channel.

Phase B ships the web adapter (the gate event on /ws/runs IS the notification).
Email and Teams adapters register here in Phase D via the same interface."""

from collections.abc import Awaitable, Callable

from app.observability.logging import get_logger
from app.orchestration.events import get_event_bus
from app.orchestration.state import PipelineState

log = get_logger(__name__)

# channel name → async adapter(state, gate, csv_key, message)
Adapter = Callable[[PipelineState, int, str, str], Awaitable[None]]
_adapters: dict[str, Adapter] = {}


def register_adapter(channel: str, adapter: Adapter) -> None:
    _adapters[channel] = adapter


async def notify_gate(
    state: PipelineState, *, gate: int, csv_key: str, message: str, csv_sha: str = ""
) -> None:
    channel = state.get("origin_channel", "web")
    run_id = state.get("run_id")

    # LangGraph re-executes an interrupted node on resume — dedupe by content hash
    # so the same CSV never notifies twice, while a changed CSV (post-"changes"
    # loop) notifies again.
    if run_id and csv_sha:
        import redis.asyncio as aioredis

        from app.config.settings import get_settings

        r = aioredis.from_url(get_settings().redis_url, decode_responses=True)
        try:
            fresh = await r.set(f"notify:{run_id}:g{gate}:{csv_sha[:16]}", "1",
                                nx=True, ex=48 * 3600)
        finally:
            await r.aclose()
        if not fresh:
            log.info("notifier.deduped", gate=gate, run_id=run_id)
            return

    if run_id:
        await get_event_bus().emit(
            run_id, "notification_sent", node=f"gate{gate}",
            payload={"channel": channel, "csv_key": csv_key, "message": message},
        )

    adapter = _adapters.get(channel)
    if adapter is None:
        # web/scheduler origins observe the run stream; nothing else to deliver yet
        log.info("notifier.no_adapter", channel=channel, gate=gate)
        return
    await adapter(state, gate, csv_key, message)
