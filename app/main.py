import asyncio
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.admin import router as admin_router
from app.api.routes.runs import router as runs_router
from app.api.ws.query_builder import router as ws_qb_router
from app.api.ws.runs_stream import router as ws_runs_router
from app.observability.logging import get_logger, setup_logging
from app.observability.tracing import setup_tracing

# psycopg async (LangGraph checkpointer) cannot use Windows' ProactorEventLoop;
# harmless no-op in the Linux container.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

log = get_logger(__name__)


def _wire_observability_hooks() -> None:
    """Memory ops and self-heal steps become run events when a run id is present."""
    from app.memory import mem0_service
    from app.orchestration import self_heal
    from app.orchestration.context import current_run_id
    from app.orchestration.events import get_event_bus

    async def memory_hook(op: str, agent: str, mtype: str, scope: str, summary: str) -> None:
        run_id = current_run_id.get()
        if run_id:
            await get_event_bus().emit(
                run_id, "memory_op", node=agent,
                payload={"op": op, "type": mtype, "scope": scope, "summary": summary},
            )

    async def heal_hook(event: str, payload: dict) -> None:
        run_id = current_run_id.get()
        if run_id:
            await get_event_bus().emit(run_id, event, node=payload.get("agent"), payload=payload)

    mem0_service.set_memory_event_hook(memory_hook)
    self_heal.set_heal_event_hook(heal_hook)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.startup")
    try:
        from app.orchestration.checkpoint import setup_checkpointer_tables
        from app.orchestration.run_manager import get_run_manager

        await setup_checkpointer_tables()
        recovered = await get_run_manager().recovery_sweep()
        if recovered:
            log.info("app.recovered_runs", count=recovered)
    except Exception as exc:
        # infra warm-up problems surface via /health, not a crashed process
        log.error("app.startup_degraded", error=str(exc))
    _wire_observability_hooks()
    yield
    log.info("app.shutdown")


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="PR Intelligence Agent", version="0.1.0", lifespan=lifespan)
    setup_tracing(app)
    app.include_router(admin_router)
    app.include_router(runs_router)
    app.include_router(ws_runs_router)
    app.include_router(ws_qb_router)
    return app


app = create_app()
