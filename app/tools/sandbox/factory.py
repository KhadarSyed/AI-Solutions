from functools import lru_cache

from app.config.settings import get_settings
from app.tools.sandbox.base import SandboxExecutor


@lru_cache
def get_sandbox() -> SandboxExecutor:
    backend = get_settings().sandbox_backend
    if backend == "docker":
        from app.tools.sandbox.docker_exec import DockerExecSandbox

        return DockerExecSandbox()
    if backend == "e2b":
        raise NotImplementedError(
            "E2B sandbox is a planned drop-in: implement E2BSandbox against "
            "app.tools.sandbox.base.SandboxExecutor and register it here."
        )
    raise ValueError(f"unknown SANDBOX_BACKEND: {backend}")
