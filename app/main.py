from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes.admin import router as admin_router
from app.observability.logging import get_logger, setup_logging
from app.observability.tracing import setup_tracing

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("app.startup")
    yield
    log.info("app.shutdown")


def create_app() -> FastAPI:
    setup_logging()
    app = FastAPI(title="PR Intelligence Agent", version="0.1.0", lifespan=lifespan)
    setup_tracing(app)
    app.include_router(admin_router)
    return app


app = create_app()
