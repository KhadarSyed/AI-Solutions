"""RunManager — concurrent graph execution with pause/resume/restart/cancel,
heartbeats, per-session mutual exclusion, and startup crash recovery."""

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime, timedelta
from functools import lru_cache

from langgraph.types import Command
from sqlalchemy import select, update

from app.config.settings import get_settings
from app.db.base import get_sessionmaker
from app.db.models import Run
from app.observability.logging import get_logger
from app.orchestration.checkpoint import checkpointer
from app.orchestration.events import get_event_bus
from app.orchestration.graphs.pipeline_graph import build_pipeline_graph
from app.orchestration.state import PipelineState

log = get_logger(__name__)

HEARTBEAT_SECONDS = 15
STALE_AFTER_SECONDS = 60

GRAPH_BUILDERS = {
    "pipeline": build_pipeline_graph,
}


class RunManager:
    def __init__(self):
        self._tasks: dict[str, asyncio.Task] = {}
        self._pausing: set[str] = set()
        self._sem = asyncio.Semaphore(get_settings().max_concurrent_runs)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def start(
        self,
        *,
        graph_name: str,
        input_state: PipelineState,
        session_id: str | None = None,
        origin_channel: str = "web",
        origin_address: dict | None = None,
    ) -> str:
        if graph_name not in GRAPH_BUILDERS:
            raise ValueError(f"unknown graph: {graph_name}")

        if session_id and await self._session_busy(session_id):
            raise RuntimeError(f"session {session_id} already has an active run")

        run_id = uuid.uuid4()
        thread_id = f"run-{run_id}"
        # human-friendly Task ID, stored on the run (origin_address) so gate replies
        # can be matched to the exact task and every subject carries it
        from app.orchestration.task_id import make_task_id

        task_id = input_state.get("task_id") or await make_task_id(input_state.get("brand", ""))
        addr = dict(origin_address or {})
        addr.setdefault("task_id", task_id)
        async with get_sessionmaker()() as db, db.begin():
            db.add(
                Run(
                    id=run_id, session_id=session_id, graph_name=graph_name,
                    thread_id=thread_id, status="running",
                    origin_channel=origin_channel, origin_address=addr,
                    heartbeat_at=datetime.now(UTC),
                )
            )

        state: PipelineState = {
            **input_state,
            "run_id": str(run_id),
            "task_id": task_id,
            "origin_channel": origin_channel,
            "origin_address": addr,
        }
        self._spawn(str(run_id), thread_id, graph_name, state)
        await get_event_bus().emit(run_id, "run_started",
                                   payload={"graph": graph_name, "task_id": task_id})
        return str(run_id)

    async def resume(self, run_id: str, resume_payload: dict | None = None) -> None:
        run = await self._get(run_id)
        if run.status not in ("awaiting_human", "paused", "interrupted"):
            raise RuntimeError(f"run {run_id} is {run.status}, not resumable")
        if run.status == "awaiting_human" and resume_payload is None:
            raise RuntimeError("awaiting_human run needs a resume payload (gate decision)")

        await self._set_status(run_id, "running", awaiting_input=None)
        payload = Command(resume=resume_payload) if resume_payload is not None else None
        self._spawn(run_id, run.thread_id, run.graph_name, payload)
        await get_event_bus().emit(run_id, "run_resumed", payload={"input": resume_payload or {}})

    async def pause(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task is None or task.done():
            raise RuntimeError(f"run {run_id} is not executing")
        self._pausing.add(run_id)
        task.cancel()
        await get_event_bus().emit(run_id, "run_paused")

    async def cancel(self, run_id: str) -> None:
        task = self._tasks.get(run_id)
        if task and not task.done():
            task.cancel()
        await self._set_status(run_id, "cancelled")
        await get_event_bus().emit(run_id, "run_cancelled")

    async def restart(self, run_id: str, input_state: PipelineState) -> str:
        """Fresh execution on a new thread for the same session/channel context."""
        run = await self._get(run_id)
        with contextlib.suppress(Exception):
            await self.cancel(run_id)
        return await self.start(
            graph_name=run.graph_name,
            input_state=input_state,
            session_id=str(run.session_id) if run.session_id else None,
            origin_channel=run.origin_channel,
            origin_address=run.origin_address,
        )

    # ── execution ────────────────────────────────────────────────────────────

    def _spawn(self, run_id: str, thread_id: str, graph_name: str, graph_input) -> None:
        self._tasks[run_id] = asyncio.create_task(
            self._execute(run_id, thread_id, graph_name, graph_input)
        )

    async def _stream_graph(self, run_id: str, thread_id: str, graph_name: str,
                            graph_input) -> None:
        """Run/resume the graph, emitting node + gate events; sets the terminal
        status. Raised exceptions propagate to the caller for self-heal."""
        bus = get_event_bus()
        async with self._sem, checkpointer() as saver:
            graph = GRAPH_BUILDERS[graph_name](saver)
            cfg = {"configurable": {"thread_id": thread_id}}
            interrupted_payload: dict | None = None

            async for update in graph.astream(graph_input, cfg, stream_mode="updates"):
                for node, delta in update.items():
                    if node == "__interrupt__":
                        intr = delta[0] if isinstance(delta, tuple | list) else delta
                        interrupted_payload = getattr(intr, "value", {}) or {}
                        await bus.emit(run_id, "gate_raised", node="gate",
                                       payload=interrupted_payload)
                    else:
                        await bus.emit(run_id, "node_finished", node=node,
                                       payload={"delta": _summarize(delta)})

            if interrupted_payload is not None:
                await self._set_status(run_id, "awaiting_human",
                                       awaiting_input=interrupted_payload)
                await bus.emit(run_id, "awaiting_human", payload=interrupted_payload)
            else:
                await self._set_status(run_id, "completed")
                await bus.emit(run_id, "run_completed")

    async def _try_heal(self, run_id: str, thread_id: str, graph_name: str,
                        graph_input, exc: Exception) -> bool:
        """Run the self-heal ladder on a run failure. The only executor-level
        remedy is a checkpoint-resume retry with backoff — resuming re-runs the
        failed node from the last checkpoint."""
        from app.orchestration.self_heal import RemedyAction, attempt_heal

        project_id = graph_input.get("project_id", "") if isinstance(graph_input, dict) else ""

        async def _retry() -> bool:
            await asyncio.sleep(2)
            try:
                await self._stream_graph(run_id, thread_id, graph_name, None)  # resume
                return True
            except Exception as retry_exc:
                log.info("run.heal_retry_failed", run_id=run_id, error=str(retry_exc)[:200])
                return False

        try:
            res = await attempt_heal(
                agent=graph_name, project_id=project_id, error_text=str(exc),
                apply={RemedyAction.RETRY_WITH_BACKOFF: _retry},
            )
            return res.healed
        except Exception as heal_exc:
            log.info("run.heal_error", run_id=run_id, error=str(heal_exc)[:200])
            return False

    async def _execute(self, run_id: str, thread_id: str, graph_name: str, graph_input) -> None:
        from app.orchestration.context import current_run_id

        current_run_id.set(run_id)
        bus = get_event_bus()
        hb = asyncio.create_task(self._heartbeat_loop(run_id))
        try:
            await self._stream_graph(run_id, thread_id, graph_name, graph_input)
        except asyncio.CancelledError:
            status = "paused" if run_id in self._pausing else "cancelled"
            self._pausing.discard(run_id)
            await self._set_status(run_id, status)
            raise
        except Exception as exc:
            log.error("run.failed", run_id=run_id, error=str(exc))
            if await self._try_heal(run_id, thread_id, graph_name, graph_input, exc):
                log.info("run.self_healed", run_id=run_id)
            else:
                await self._set_status(run_id, "failed", error=str(exc)[:2000])
                await bus.emit(run_id, "run_failed", payload={"error": str(exc)[:500]})
        finally:
            hb.cancel()
            self._tasks.pop(run_id, None)

    async def _heartbeat_loop(self, run_id: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_SECONDS)
            async with get_sessionmaker()() as db, db.begin():
                await db.execute(
                    update(Run).where(Run.id == uuid.UUID(run_id))
                    .values(heartbeat_at=datetime.now(UTC))
                )

    # ── recovery & queries ───────────────────────────────────────────────────

    async def recovery_sweep(self) -> int:
        """Mark crashed 'running' rows (stale heartbeat, no live task) interrupted."""
        cutoff = datetime.now(UTC) - timedelta(seconds=STALE_AFTER_SECONDS)
        async with get_sessionmaker()() as db, db.begin():
            rows = (
                (await db.execute(select(Run).where(Run.status == "running"))).scalars().all()
            )
            stale = [
                r for r in rows
                if str(r.id) not in self._tasks
                and (r.heartbeat_at is None or r.heartbeat_at < cutoff)
            ]
            for r in stale:
                r.status = "interrupted"
        for r in stale:
            await get_event_bus().emit(r.id, "run_recovered_as_interrupted")
        if stale:
            log.info("run.recovery_sweep", recovered=len(stale))
        return len(stale)

    async def _session_busy(self, session_id: str) -> bool:
        async with get_sessionmaker()() as db:
            row = (
                await db.execute(
                    select(Run.id).where(
                        Run.session_id == session_id,
                        Run.status.in_(("running", "awaiting_human", "paused")),
                    )
                )
            ).first()
        return row is not None

    async def _get(self, run_id: str) -> Run:
        async with get_sessionmaker()() as db:
            run = (
                await db.execute(select(Run).where(Run.id == uuid.UUID(run_id)))
            ).scalar_one_or_none()
        if run is None:
            raise RuntimeError(f"run {run_id} not found")
        return run

    async def _set_status(self, run_id: str, status: str, *, error: str | None = None,
                          awaiting_input: dict | None = ...) -> None:
        values: dict = {"status": status}
        if error is not None:
            values["error"] = error
        if awaiting_input is not ...:
            values["awaiting_input"] = awaiting_input
        async with get_sessionmaker()() as db, db.begin():
            await db.execute(update(Run).where(Run.id == uuid.UUID(run_id)).values(**values))


def _summarize(delta) -> dict:
    if isinstance(delta, dict):
        return {k: (v if isinstance(v, int | float | bool | str) else str(v)[:200])
                for k, v in delta.items()}
    return {"value": str(delta)[:200]}


@lru_cache
def get_run_manager() -> RunManager:
    return RunManager()
