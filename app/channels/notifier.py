"""Channel-aware delivery — always to the run's ORIGIN channel.

Gate CSVs and the final result go back to wherever the request came from (email
thread, Teams chat, or Teams channel). Web/scheduler origins observe /ws/runs.
"""

from app.channels.base import ChannelAdapter, OutboundMessage
from app.observability.logging import get_logger
from app.orchestration.events import get_event_bus
from app.orchestration.state import PipelineState

log = get_logger(__name__)

# origin_channel value ("email" | "teams_chat" | "teams_channel") → adapter
_adapters: dict[str, ChannelAdapter] = {}


def register_adapter(channel: str, adapter: ChannelAdapter) -> None:
    _adapters[channel] = adapter


async def _deliver(state: PipelineState, event: str, message: str,
                   attachments: list[tuple[str, bytes, str]], subject: str) -> None:
    channel = state.get("origin_channel", "web")
    run_id = state.get("run_id")
    if run_id:
        await get_event_bus().emit(
            run_id, event, payload={"channel": channel, "message": message[:400],
                                    "attachments": [a[0] for a in attachments]},
        )
    adapter = _adapters.get(channel)
    if adapter is None:
        log.info("notifier.no_adapter", channel=channel)  # web/scheduler: stream only
        return
    await adapter.send(
        state.get("origin_address", {}),
        OutboundMessage(subject=subject, text=message, attachments=attachments),
    )


async def notify_gate(state: PipelineState, *, gate: int, csv_key: str, message: str,
                      csv_sha: str = "") -> None:
    # dedupe: LangGraph re-executes an interrupted node on resume
    run_id = state.get("run_id")
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

    attachments: list[tuple[str, bytes, str]] = []
    channel = state.get("origin_channel", "web")
    if channel != "web":
        from app.artifacts.factory import get_artifact_store

        try:
            data = await get_artifact_store().get_bytes(csv_key)
            attachments = [(csv_key.rsplit("/", 1)[-1], data, "text/csv")]
        except Exception as exc:
            log.info("notifier.csv_load_failed", error=str(exc)[:120])

    await _deliver(state, "notification_sent", message, attachments,
                   subject=f"Gate {gate} — action needed")


async def notify_complete(state: PipelineState) -> None:
    """Deliver the finished analysis (branded report) to the origin channel."""
    channel = state.get("origin_channel", "web")
    brand = state.get("brand", "your brand")
    session_id = state.get("session_id", "")
    message = (f"Your {brand} media analysis is complete — {state.get('approved_count', 0)} "
               f"approved articles across {state.get('tagged_count', 0)} tagged. "
               "The branded report is attached; dashboards are ready in the console.")
    attachments: list[tuple[str, bytes, str]] = []
    if channel != "web":
        import contextlib

        with contextlib.suppress(Exception):
            from app.agents.report_builder import build_report

            data = await build_report(session_id=session_id, brand=brand)
            attachments = [(f"{brand.lower()}_report.docx", data,
                            "application/vnd.openxmlformats-officedocument."
                            "wordprocessingml.document")]
    await _deliver(state, "result_delivered", message, attachments,
                   subject=f"{brand} — media analysis complete")
