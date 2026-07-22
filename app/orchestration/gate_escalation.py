"""Pending-gate escalation.

When a run is parked at a human approval gate, this loop re-notifies the approver on a
fixed cadence (up to gate_max_reminders times). If there's still no reply, it drops
(cancels) the run and moves on — a stalled task never holds a run slot forever, and the
other pending tasks keep flowing because runs are independent under the RunManager.

State per run lives in Redis: gate:last:{run_id} (epoch of the last notification) and
gate:rem:{run_id} (reminders sent so far). Both are cleared when the run leaves the gate.
"""
import asyncio
import contextlib
import time

import redis.asyncio as aioredis
from sqlalchemy import select

from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Run
from app.db.models import Session as SessionRow
from app.observability.logging import get_logger

log = get_logger(__name__)

SWEEP_SECONDS = 60
# gate index → user-facing stage name (reminders read "Stage 3 · Tagged", not "the 2 gate")
_GATE_STAGE = {0: "Stage 1 · Plan", 1: "Stage 2 · Collection KPIs", 2: "Stage 3 · Tagged Results"}


async def _brand_for(session_id) -> str:
    with contextlib.suppress(Exception):
        async with get_sessionmaker()() as db:
            s = await db.get(SessionRow, session_id)
            return (s.config or {}).get("brand", "") if s else ""
    return ""


def _state_from_run(run: Run, brand: str) -> dict:
    return {
        "run_id": str(run.id),
        "task_id": (run.origin_address or {}).get("task_id"),
        "origin_channel": run.origin_channel,
        "origin_address": run.origin_address or {},
        "brand": brand,
    }


async def _remind(run: Run, brand: str, n: int, of: int) -> None:
    from app.channels.base import OutboundMessage
    from app.channels.notifier import (
        _adapters,  # origin_channel → adapter
        _send_threaded,
        _task_cc,
        _task_subject,
    )
    from app.security.auth import resume_token

    adapter = _adapters.get(run.origin_channel)
    if adapter is None:                       # web/scheduler origin — stream only, nothing to email
        return
    state = _state_from_run(run, brand)
    # user-facing stage name, not the internal gate index ("the 2 gate")
    stage = _GATE_STAGE.get((run.awaiting_input or {}).get("gate"), "the pending review")
    token = resume_token(str(run.id))
    tid = state.get("task_id") or ""
    text = (f"⏰ Reminder {n} of {of}: task {tid} ({brand}) is waiting for your approval at "
            f"{stage}. Reply APPROVE in this thread to continue (or reply with changes). "
            f"Reference {token}. If I don't hear back, I'll pause this task and move on.")
    with contextlib.suppress(Exception):
        await _send_threaded(state, adapter, OutboundMessage(
            subject=_task_subject(state), text=text, cc=await _task_cc(state)))
    log.info("gate.reminder_sent", run_id=str(run.id), task_id=tid, reminder=n, of=of)


async def _drop(run: Run, brand: str) -> None:
    from app.channels.base import OutboundMessage
    from app.channels.notifier import _adapters, _send_threaded, _task_cc, _task_subject
    from app.orchestration.run_manager import get_run_manager

    with contextlib.suppress(Exception):
        await get_run_manager().cancel(str(run.id))
    state = _state_from_run(run, brand)
    adapter = _adapters.get(run.origin_channel)
    if adapter is not None:
        tid = state.get("task_id") or ""
        text = (f"⛔ Task {tid} ({brand}) was paused after no approval to the reminders. "
                f"No work was lost — reply to this thread any time and I'll resume it, or send "
                f"'Monitor {brand}' to start fresh.")
        with contextlib.suppress(Exception):
            await _send_threaded(state, adapter, OutboundMessage(
                subject=_task_subject(state), text=text, cc=await _task_cc(state)))
    log.info("gate.dropped", run_id=str(run.id), task_id=state.get("task_id"))


async def _sweep(r) -> None:
    s = get_settings()
    interval = max(60, s.gate_reminder_minutes * 60)
    max_rem = max(0, s.gate_max_reminders)
    now = time.time()

    async with get_sessionmaker()() as db:
        runs = (await db.execute(
            select(Run).where(Run.status == "awaiting_human"))).scalars().all()
    live_ids = {str(run.id) for run in runs}

    for run in runs:
        rid = str(run.id)
        last = await r.get(f"gate:last:{rid}")
        if last is None:                       # first time we see this gate — start the clock
            await r.set(f"gate:last:{rid}", str(now), ex=14 * 24 * 3600)
            await r.set(f"gate:rem:{rid}", "0", ex=14 * 24 * 3600)
            continue
        if now - float(last) < interval:
            continue
        rem = int(await r.get(f"gate:rem:{rid}") or 0)
        brand = await _brand_for(run.session_id) or "your brand"
        if rem < max_rem:
            await _remind(run, brand, rem + 1, max_rem)
            await r.set(f"gate:rem:{rid}", str(rem + 1), ex=14 * 24 * 3600)
            await r.set(f"gate:last:{rid}", str(now), ex=14 * 24 * 3600)
        else:
            await _drop(run, brand)
            await r.delete(f"gate:last:{rid}", f"gate:rem:{rid}")

    # clear state for runs that have left the gate (approved/completed/cancelled)
    with contextlib.suppress(Exception):
        async for key in r.scan_iter(match="gate:last:*"):
            if key.split(":")[-1] not in live_ids:
                rid = key.split(":")[-1]
                await r.delete(f"gate:last:{rid}", f"gate:rem:{rid}")


async def gate_escalation_loop() -> None:
    s = get_settings()
    if not s.gate_escalation_enabled:
        log.info("gate.escalation_disabled")
        return
    r = aioredis.from_url(s.redis_url, decode_responses=True)
    log.info("gate.escalation_started", every_min=s.gate_reminder_minutes,
             max_reminders=s.gate_max_reminders)
    while True:
        with contextlib.suppress(asyncio.CancelledError), contextlib.suppress(Exception):
            await _sweep(r)
        await asyncio.sleep(SWEEP_SECONDS)
