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


def _task_subject(state: PipelineState) -> str:
    """ONE subject per task — every message (acks, gates, status, completion) shares
    it so the whole task collapses into a single email thread. The Task-ID tag also
    keeps different tasks (BeOne vs Trane vs a repeat) in separate threads."""
    tid = state.get("task_id") or (state.get("origin_address") or {}).get("task_id")
    brand = state.get("brand") or "Monitoring"
    base = f"{brand} Monitoring"
    return f"[{tid}] {base}" if tid else base


# kept for callers that still pass an explicit base; task threads use _task_subject
def _subject(state: PipelineState, base: str) -> str:
    tid = state.get("task_id") or (state.get("origin_address") or {}).get("task_id")
    return f"[{tid}] {base}" if tid else base


_AGENT_ICON = {"WebSearch": "🔎", "Tagging": "🏷️", "Dashboard": "📊"}


async def notify_agent(state: PipelineState, agent: str, phase: str, message: str) -> None:
    """Per-agent start/finish status to the run's origin channel (email included) +
    the run stream. phase is 'started' or 'finished'. Threaded under the task."""
    import contextlib

    run_id = state.get("run_id")
    if run_id:
        await get_event_bus().emit(run_id, "agent_status", node=agent,
                                   payload={"phase": phase, "message": message[:300]})
    adapter = _adapters.get(state.get("origin_channel", "web"))
    if adapter is None:
        return
    icon = _AGENT_ICON.get(agent, "•")
    with contextlib.suppress(Exception):
        await adapter.send(state.get("origin_address", {}),
                           OutboundMessage(subject=_task_subject(state),
                                           text=f"{icon} {agent} Agent {phase} — {message}"))


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
                   subject=_task_subject(state))


async def notify_progress(state: PipelineState, message: str) -> None:
    """Short 'what I'm doing now' status to the origin channel. Emitted to the
    run stream always; delivered as a message to Teams (chat/channel) so the
    requester sees live stage updates. Email origins get gates + completion only
    (no inbox spam)."""
    channel = state.get("origin_channel", "web")
    run_id = state.get("run_id")
    if run_id:
        await get_event_bus().emit(run_id, "progress",
                                   payload={"channel": channel, "message": message[:300]})
    if channel in ("teams_chat", "teams_channel"):
        adapter = _adapters.get(channel)
        if adapter is not None:
            import contextlib
            with contextlib.suppress(Exception):
                await adapter.send(state.get("origin_address", {}),
                                   OutboundMessage(subject="Progress", text=message))


async def notify_complete(state: PipelineState) -> None:
    """Final stage — deliver the dashboard + tagged CSV + branded report to the
    origin channel (same email thread), then invite follow-up questions."""
    import contextlib

    channel = state.get("origin_channel", "web")
    brand = state.get("brand", "your brand")
    session_id = state.get("session_id", "")
    slug = brand.lower().replace(" ", "_")
    online = (f"🔗 View online anytime: {state['dashboard_url']}\n\n"
              if state.get("dashboard_url") else "")
    message = (
        f"Your {brand} media analysis is complete — {state.get('approved_count', 0)} "
        f"approved articles across {state.get('tagged_count', 0)} tagged.\n\n"
        f"{online}"
        "Attached:\n"
        "• dashboard.html — the interactive dashboard (open in any browser)\n"
        f"• {slug}_tagged_articles.csv — the full tagged dataset\n"
        f"• {slug}_report.docx — the branded narrative report\n\n"
        "Reply to this thread with any questions and I'll answer them from the "
        "analyzed coverage."
    )
    attachments: list[tuple[str, bytes, str]] = []
    if channel != "web":
        from app.artifacts import keys
        from app.artifacts.factory import get_artifact_store

        store = get_artifact_store()

        # the dashboard itself (self-contained HTML) — skip if too large to inline
        with contextlib.suppress(Exception):
            html = await store.get_bytes(f"reports/{session_id}/dashboard.html")
            if len(html) <= 3_500_000:
                attachments.append(("dashboard.html", html, "text/html"))
            else:
                log.info("notifier.dashboard_too_large", bytes=len(html))

        # the tagged CSV (the "csv file" delivered alongside the dashboard)
        with contextlib.suppress(Exception):
            from app.channels.csv_export import TAGGED_COLUMNS, articles_to_csv

            payload = await store.get_json(keys.tagged_file(session_id))
            csv_bytes = articles_to_csv(payload.get("articles", []), TAGGED_COLUMNS)
            attachments.append((f"{slug}_tagged_articles.csv", csv_bytes, "text/csv"))

        # the branded report
        with contextlib.suppress(Exception):
            from app.agents.report_builder import build_report

            data = await build_report(session_id=session_id, brand=brand)
            attachments.append((f"{slug}_report.docx", data,
                                "application/vnd.openxmlformats-officedocument."
                                "wordprocessingml.document"))
    await _deliver(state, "result_delivered", message, attachments,
                   subject=_task_subject(state))
