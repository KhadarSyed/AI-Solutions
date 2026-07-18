"""Execution context — the active run id, visible to any code running inside a
run's task (memory hooks, heal hooks, agents) without threading it through
every signature."""

import contextvars

current_run_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_run_id", default=None
)
