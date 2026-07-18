"""Optional tracing backends — both no-ops without their env keys.

Logfire instruments Pydantic AI, FastAPI, and httpx.
LangSmith picks up LangGraph runs automatically once its env vars are set.
"""

import os

from app.config.settings import get_settings
from app.observability.logging import get_logger

log = get_logger(__name__)


def setup_tracing(app=None) -> None:
    settings = get_settings()

    if settings.logfire_token:
        import logfire

        logfire.configure(token=settings.logfire_token, service_name="pr-intelligence-agent")
        logfire.instrument_pydantic_ai()
        logfire.instrument_httpx()
        if app is not None:
            logfire.instrument_fastapi(app)
        log.info("tracing.logfire_enabled")

    if settings.langsmith_api_key:
        os.environ.setdefault("LANGSMITH_TRACING", "true")
        os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
        os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
        log.info("tracing.langsmith_enabled", project=settings.langsmith_project)
