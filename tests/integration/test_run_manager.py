"""Execution layer: RunManager lifecycle + EventBus capture on the stub pipeline."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from app.db.base import get_sessionmaker
from app.db.models import Run
from app.orchestration.checkpoint import setup_checkpointer_tables
from app.orchestration.events import get_event_bus
from app.orchestration.run_manager import get_run_manager


async def _wait_status(run_id: str, statuses: set[str], timeout: float = 90.0) -> str:
    # generous: the dashboards node runs the Dashboard Agent (logo lookups + LLM summaries)
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        async with get_sessionmaker()() as db:
            status = (
                await db.execute(select(Run.status).where(Run.id == uuid.UUID(run_id)))
            ).scalar_one()
        if status in statuses:
            return status
        await asyncio.sleep(0.2)
    raise AssertionError(f"run {run_id} never reached {statuses}, last={status}")


async def test_full_lifecycle_with_gates_and_events(blank_session):
    await setup_checkpointer_tables()
    rm = get_run_manager()

    run_id = await rm.start(graph_name="pipeline", input_state=blank_session)
    assert await _wait_status(run_id, {"awaiting_human"}) == "awaiting_human"

    # first gate raised is the PLAN gate (0), persisted on the run row
    async with get_sessionmaker()() as db:
        run = (await db.execute(select(Run).where(Run.id == uuid.UUID(run_id)))).scalar_one()
    assert run.awaiting_input["gate"] == 0

    # approve plan → collection gate, approve → tagged gate, approve → complete
    await rm.resume(run_id, {"decision": "approved"})
    assert await _wait_status(run_id, {"awaiting_human"}) == "awaiting_human"

    await rm.resume(run_id, {"decision": "approved"})
    assert await _wait_status(run_id, {"awaiting_human"}) == "awaiting_human"

    await rm.resume(run_id, {"decision": "approved"})
    assert await _wait_status(run_id, {"completed"}) == "completed"

    events = await get_event_bus().replay(run_id)
    types = [e["event_type"] for e in events]
    assert types[0] == "run_started"
    assert "gate_raised" in types
    assert types.count("awaiting_human") == 3
    assert types[-1] == "run_completed"
    # ordered, gapless sequence
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(1, len(seqs) + 1))
    nodes_finished = [e["node"] for e in events if e["event_type"] == "node_finished"]
    assert "collect" in nodes_finished and "reflect" in nodes_finished


async def test_session_mutex_blocks_second_run(blank_session):
    rm = get_run_manager()
    sid = blank_session["session_id"]

    run_id = await rm.start(graph_name="pipeline", input_state=blank_session, session_id=sid)
    await _wait_status(run_id, {"awaiting_human"})
    try:
        await rm.start(graph_name="pipeline", input_state=blank_session, session_id=sid)
        raise AssertionError("second run on same session must be refused")
    except RuntimeError as exc:
        assert "active run" in str(exc)
    finally:
        await rm.cancel(run_id)


async def test_recovery_sweep_marks_stale_running():
    rm = get_run_manager()
    stale_id = uuid.uuid4()
    async with get_sessionmaker()() as db, db.begin():
        db.add(
            Run(
                id=stale_id, graph_name="pipeline", thread_id=f"stale-{stale_id}",
                status="running",
                heartbeat_at=datetime.now(UTC) - timedelta(minutes=10),
            )
        )
    # auto_resume=False so we test the marking in isolation (a resume of a checkpoint-less
    # stale thread would otherwise move it off 'interrupted')
    recovered = await rm.recovery_sweep(auto_resume=False)
    assert recovered >= 1
    async with get_sessionmaker()() as db:
        status = (
            await db.execute(select(Run.status).where(Run.id == stale_id))
        ).scalar_one()
    assert status == "interrupted"
    async with get_sessionmaker()() as db, db.begin():
        await db.execute(update(Run).where(Run.id == stale_id).values(status="cancelled"))
